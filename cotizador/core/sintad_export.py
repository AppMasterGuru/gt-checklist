"""
SINTAD Excel export.

Abel re-enters every approved quote into SINTAD manually
(Meeting 3, 1:04:26). This module pre-fills every field
so the ejecutivo only has to copy-paste, not re-type.

4 sheets:
  1. Datos Generales  — shipment master data
  2. Costos           — internal cost breakdown (costeo)
  3. Venta            — sell-side line items
  4. Staff            — role assignments per SINTAD workflow

Staff logic (from Meeting 3, 1:16:09):
  Ejecutivo comercial : Jean Paul (GT-PC / GT-WCA) | Renato (others)
  Customer service    : Paulo Díaz (always)  # TODO confirm with Abel: Paolo / Pablo / Paulo — flagged 2026-06-09 demo
  Operativo           : Robin Lujan (imports) | Junior Loa (exports)
  Supervisor          : Kristel (always)
"""

from __future__ import annotations

import io
import json
from datetime import date

import openpyxl
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side

# ── Brand colours ────────────────────────────────────────────────────────────
_ORANGE = "E8471C"
_NAVY   = "1B3A6B"
_GREY   = "D9D9D9"
_WHITE  = "FFFFFF"
_BLACK  = "000000"


def _hdr_fill(colour: str) -> PatternFill:
    return PatternFill("solid", fgColor=colour)


def _bold(size: int = 11, colour: str = _BLACK) -> Font:
    return Font(bold=True, size=size, color=colour)


def _thin_border() -> Border:
    s = Side(style="thin", color=_BLACK)
    return Border(left=s, right=s, top=s, bottom=s)


def _write_kv(ws, start_row: int, pairs: list[tuple[str, object]]) -> int:
    """Write (label, value) rows. Returns next available row."""
    border = _thin_border()
    for label, value in pairs:
        c_label = ws.cell(row=start_row, column=1, value=label)
        c_label.font   = _bold()
        c_label.fill   = _hdr_fill(_GREY)
        c_label.border = border
        c_label.alignment = Alignment(wrap_text=True)

        c_val = ws.cell(row=start_row, column=2, value=value)
        c_val.border    = border
        c_val.alignment = Alignment(wrap_text=True)

        start_row += 1
    return start_row


def _write_table(ws, start_row: int, headers: list[str], rows: list[list]) -> int:
    """Write a header row + data rows. Returns next available row."""
    border = _thin_border()
    for col, h in enumerate(headers, 1):
        c = ws.cell(row=start_row, column=col, value=h)
        c.font   = Font(bold=True, color=_WHITE, size=10)
        c.fill   = _hdr_fill(_NAVY)
        c.border = border
        c.alignment = Alignment(horizontal="center")
    start_row += 1

    for row in rows:
        for col, val in enumerate(row, 1):
            c = ws.cell(row=start_row, column=col, value=val)
            c.border = border
            if isinstance(val, (int, float)):
                c.alignment = Alignment(horizontal="right")
        start_row += 1

    return start_row


def _parse(raw) -> dict:
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, str) and raw:
        try:
            return json.loads(raw)
        except Exception:
            pass
    return {}


def _is_export(quote: dict) -> bool:
    origin = (quote.get("origin") or "").lower()
    return any(k in origin for k in ["lima", "peru", "callao", "lim"])


def _staff(quote: dict) -> dict:
    staff_code = (quote.get("staff_code") or "GT-PC").upper()
    is_export  = _is_export(quote)

    ejecutivo = "Jean Paul" if staff_code in ("GT-PC", "GT-WCA") else "Renato"
    operativo = "Junior Loa" if is_export else "Robin Lujan"

    return {
        "Ejecutivo Comercial": ejecutivo,
        "Customer Service":    "Paulo Díaz",  # TODO confirm with Abel: Paolo / Pablo / Paulo — flagged 2026-06-09 demo
        "Operativo":           operativo,
        "Supervisor":          "Kristel",
    }


def generate_sintad_excel(quote: dict) -> bytes:
    """
    Build a SINTAD pre-fill Excel workbook from a quote dict.
    Returns raw bytes suitable for a Flask send_file() response.
    """
    costeo = _parse(quote.get("costeo_json"))
    venta  = _parse(quote.get("venta_json"))
    dims   = _parse(quote.get("dimensions_json"))

    ref        = quote.get("reference_code") or ""
    today      = date.today().strftime("%d/%m/%Y")
    validity   = venta.get("validity_days", 15)
    exc_rate   = quote.get("exchange_rate") or 0.0
    mode       = (quote.get("mode") or "lcl").upper()
    incoterm   = (quote.get("incoterm") or "").upper()
    is_export  = _is_export(quote)
    direction  = "Exportación" if is_export else "Importación"

    # Cargo type inference
    cargo_desc = (quote.get("cargo_description") or "").lower()
    if "peligrosa" in cargo_desc or "hazmat" in cargo_desc:
        cargo_type = "Mercancía Peligrosa"
    elif "perecible" in cargo_desc or "refrigerado" in cargo_desc:
        cargo_type = "Refrigerada / Temperatura controlada"
    elif mode == "FCL":
        cargo_type = "Contenedor (FCL)"
    elif mode == "LCL":
        cargo_type = "Carga Consolidada (LCL)"
    else:
        cargo_type = "Carga Aérea"

    wb = openpyxl.Workbook()

    # ── Sheet 1: Datos Generales ─────────────────────────────────────────────
    ws1 = wb.active
    ws1.title = "Datos Generales"
    ws1.column_dimensions["A"].width = 28
    ws1.column_dimensions["B"].width = 40

    # Title
    title = ws1.cell(row=1, column=1,
                     value=f"SINTAD — Datos Generales: {ref}")
    title.font = Font(bold=True, size=13, color=_WHITE)
    title.fill = _hdr_fill(_NAVY)
    ws1.merge_cells("A1:B1")
    title.alignment = Alignment(horizontal="center")

    sub = ws1.cell(row=2, column=1,
                   value=f"Generado: {today} · GT Cotizador v1.0")
    sub.font  = Font(italic=True, size=9, color=_NAVY)
    ws1.merge_cells("A2:B2")

    _write_kv(ws1, 4, [
        ("Referencia",            ref),
        ("Cliente",               quote.get("client_name") or ""),
        ("Tipo",                  direction),
        ("Puerto Origen",         quote.get("origin") or ""),
        ("Puerto Destino",        quote.get("destination") or ""),
        ("Incoterm",              incoterm),
        ("Modo de Transporte",    mode),
        ("Tipo de Carga",         cargo_type),
        ("Descripción Mercancía", quote.get("cargo_description") or ""),
        ("Peso (kg)",             quote.get("weight_kg") or 0.0),
        ("Volumen (CBM)",         round(quote.get("volume_cbm") or 0.0, 4)),
        ("Consolidador",          costeo.get("consolidator") or "—"),
        ("Agente de Aduana",      costeo.get("customs_agent") or "—"),
        ("Ruta",                  "Directa"),
        ("Días de Tránsito",      "TBD"),
        ("Frecuencia",            "Semanal"),
        ("Validez (días)",        validity),
        ("Tipo de Cambio SBS",    exc_rate),
        ("Fecha Cotización",      today),
    ])

    # ── Sheet 2: Costos ──────────────────────────────────────────────────────
    ws2 = wb.create_sheet("Costos")
    ws2.column_dimensions["A"].width = 35
    ws2.column_dimensions["B"].width = 16
    ws2.column_dimensions["C"].width = 12
    ws2.column_dimensions["D"].width = 16

    t = ws2.cell(row=1, column=1, value=f"Costos — {ref}")
    t.font = Font(bold=True, size=13, color=_WHITE)
    t.fill = _hdr_fill(_ORANGE)
    ws2.merge_cells("A1:D1")
    t.alignment = Alignment(horizontal="center")

    note = ws2.cell(row=2, column=1,
                    value="⚠ SOLO INTERNO — No compartir con el cliente")
    note.font = Font(bold=True, color=_ORANGE)
    ws2.merge_cells("A2:D2")

    flete_val    = costeo.get("flete_internacional_usd") or 0
    flete_rate   = costeo.get("flete_rate_lcl") or 0
    flete_factor = costeo.get("flete_factor")      # None for non-LCL
    thc_usd_val  = costeo.get("thc_usd") or 0
    thc_rate_val = costeo.get("thc_rate") or 0

    # TN/M3 cell: numeric factor value (blank for non-LCL and non-W/M rows)
    tn_m3_str = f"{flete_factor:.4g}" if flete_factor else ""

    costeo_rows = [
        [
            "Flete Internacional (USD)",
            flete_rate if flete_rate else flete_val,  # rate per W/M if available, else flat
            tn_m3_str,
            flete_val,
        ],
    ]
    if thc_usd_val:
        costeo_rows.append([
            "THC / Terminal Handling (USD)",
            thc_rate_val if thc_rate_val else thc_usd_val,
            tn_m3_str,
            thc_usd_val,
        ])
    costeo_rows += [
        ["Visto Bueno (USD)",      costeo.get("visto_bueno_usd") or 0,   "", costeo.get("visto_bueno_usd") or 0],
        ["Agente de Aduana (USD)", costeo.get("customs_agent_usd") or 0, "", costeo.get("customs_agent_usd") or 0],
        ["Transporte Local (USD)", costeo.get("transport_usd") or 0,     "", costeo.get("transport_usd") or 0],
        ["Transporte Local (S/)",  costeo.get("transport_soles") or 0,   "", ""],
        ["TOTAL COSTEO (USD)",     costeo.get("total_usd") or 0,         "", costeo.get("total_usd") or 0],
        ["Tipo de Cambio SBS",     exc_rate,                             "", ""],
    ]
    _write_table(ws2, 4, ["Concepto", "Costo", "TN/M3", "Total (USD)"], costeo_rows)

    # ── Sheet 3: Venta ───────────────────────────────────────────────────────
    ws3 = wb.create_sheet("Venta")
    ws3.column_dimensions["A"].width = 35
    ws3.column_dimensions["B"].width = 12
    ws3.column_dimensions["C"].width = 16
    ws3.column_dimensions["D"].width = 16

    t3 = ws3.cell(row=1, column=1, value=f"Venta — {ref}")
    t3.font = Font(bold=True, size=13, color=_WHITE)
    t3.fill = _hdr_fill(_NAVY)
    ws3.merge_cells("A1:D1")
    t3.alignment = Alignment(horizontal="center")

    line_items  = venta.get("line_items", [])
    has_factor  = any(i.get("factor_value") is not None for i in line_items)

    if has_factor:
        venta_rows = []
        for item in line_items:
            if item.get("factor_value") is not None:
                fv = item.get("factor_value", 0)
                venta_rows.append([
                    item.get("description", ""),
                    item.get("unit_rate", 0),
                    f"{fv:.4g}" if fv else "",
                    item.get("total", 0),
                ])
            else:
                venta_rows.append([
                    item.get("description", ""),
                    item.get("unit_price", 0),
                    "",
                    item.get("total", 0),
                ])
        venta_rows.append(["TOTAL (USD)", "", "", venta.get("total_usd") or 0])
        next_r = _write_table(ws3, 3,
                              ["Concepto", "Tarifa", "TN/M3", "Total (USD)"],
                              venta_rows)
    else:
        venta_rows = [
            [
                item.get("description", ""),
                item.get("quantity", 1),
                item.get("unit_price", 0),
                item.get("total", 0),
            ]
            for item in line_items
        ]
        venta_rows.append(["TOTAL (USD)", "", "", venta.get("total_usd") or 0])
        next_r = _write_table(ws3, 3,
                              ["Concepto", "Cant.", "Precio Unit. (USD)", "Total (USD)"],
                              venta_rows)

    info = ws3.cell(row=next_r + 1, column=1,
                    value=f"Margen: {(venta.get('margin_pct') or 0) * 100:.1f}%"
                          f"  ·  Validez: {validity} días")
    info.font = Font(italic=True, size=9, color=_NAVY)
    ws3.merge_cells(f"A{next_r+1}:D{next_r+1}")

    # ── Sheet 4: Staff ───────────────────────────────────────────────────────
    ws4 = wb.create_sheet("Staff")
    ws4.column_dimensions["A"].width = 28
    ws4.column_dimensions["B"].width = 25

    t4 = ws4.cell(row=1, column=1, value=f"Asignación de Staff — {ref}")
    t4.font = Font(bold=True, size=13, color=_WHITE)
    t4.fill = _hdr_fill(_NAVY)
    ws4.merge_cells("A1:B1")
    t4.alignment = Alignment(horizontal="center")

    staff = _staff(quote)
    staff_rows = [[role, name] for role, name in staff.items()]
    next_r4 = _write_table(ws4, 3, ["Rol", "Nombre"], staff_rows)

    dir_note = ws4.cell(row=next_r4 + 1, column=1,
                        value=f"Dirección detectada: {direction}")
    dir_note.font = Font(italic=True, size=9, color=_NAVY)
    ws4.merge_cells(f"A{next_r4+1}:B{next_r4+1}")

    buf = io.BytesIO()
    wb.save(buf)
    buf.seek(0)
    return buf.read()
