"""
Branded PDF proforma generator.

RULE (from Abel's demo, 1:02:32):
  "Lo enviamos bajo un PDF, así nada más, únicamente el apartado de la venta."
  Only the VENTA section goes to the client — NEVER the costeo.

Brand: #E8471C (GT orange), #1B3A6B (GT navy)
15-day validity standard (Abel confirmed).
WeasyPrint for offline rendering — no external fonts, no Google Fonts.
"""

from __future__ import annotations

import os
from datetime import date, timedelta
from pathlib import Path

try:
    from weasyprint import HTML as _WeasyHTML
    WEASYPRINT_AVAILABLE = True
except ImportError:
    WEASYPRINT_AVAILABLE = False

from config.signatures import get_signature

_TEMPLATE_PATH = Path(__file__).parent.parent / "templates" / "proforma.html"
OUTPUT_DIR = Path(os.getenv("OUTPUT_DIR", "/tmp/gt_cotizador_output"))

VALIDITY_DAYS = 15  # standard; Abel confirmed


def _build_items_html(line_items: list[dict]) -> tuple[str, str]:
    """Return (header_html, rows_html). Header adapts to factor vs flat layout."""
    has_factor = any(item.get("factor_value") is not None for item in line_items)

    if has_factor:
        header = (
            '<tr>'
            '<th>Concepto</th>'
            '<th class="num">Tarifa</th>'
            '<th class="num">TN/M3</th>'
            '<th class="num">Total</th>'
            '</tr>'
        )
    else:
        header = (
            '<tr>'
            '<th>Concepto</th>'
            '<th class="num">Cant.</th>'
            '<th class="num">Precio Unit.</th>'
            '<th class="num">Total</th>'
            '</tr>'
        )

    rows = ""
    for item in line_items:
        desc = item.get("description", "")
        total = item.get("total") or 0
        if has_factor and item.get("factor_value") is not None:
            rate = item.get("unit_rate") or 0
            fval = item.get("factor_value", 0)
            funit = item.get("factor_unit", "")
            rows += (
                f'<tr>'
                f'<td>{desc}</td>'
                f'<td class="num">USD {rate:,.2f}/W·M</td>'
                f'<td class="num">{fval:.4g} {funit}</td>'
                f'<td class="num">USD {total:,.2f}</td>'
                f'</tr>'
            )
        elif has_factor:
            rows += (
                f'<tr>'
                f'<td>{desc}</td>'
                f'<td class="num">—</td>'
                f'<td class="num">—</td>'
                f'<td class="num">USD {total:,.2f}</td>'
                f'</tr>'
            )
        else:
            rows += (
                f'<tr>'
                f'<td>{desc}</td>'
                f'<td class="num">{item.get("quantity", 1)}</td>'
                f'<td class="num">USD {item.get("unit_price", 0):,.2f}</td>'
                f'<td class="num">USD {total:,.2f}</td>'
                f'</tr>'
            )
    return header, rows


def render_html(venta: dict, meta: dict) -> str:
    """
    Render the proforma HTML from venta data and metadata.
    venta: sell-side breakdown (never costeo)
    meta:  reference, client, origin, destination, incoterm, mode, staff info
    """
    template = _TEMPLATE_PATH.read_text(encoding="utf-8")

    today = date.today()
    validity_date = today + timedelta(days=VALIDITY_DAYS)

    placeholders = {
        "{{REFERENCE}}":      meta.get("reference", ""),
        "{{CLIENT_NAME}}":    meta.get("client_name", ""),
        "{{ORIGIN}}":         meta.get("origin", ""),
        "{{DESTINATION}}":    meta.get("destination", ""),
        "{{INCOTERM}}":       meta.get("incoterm", ""),
        "{{MODE}}":           meta.get("mode", "").upper(),
        "{{DATE}}":           today.strftime("%d/%m/%Y"),
        "{{VALIDITY_DATE}}":  validity_date.strftime("%d/%m/%Y"),
        "{{VALIDITY_DAYS}}":  str(VALIDITY_DAYS),
        "{{TRANSIT_TIME}}":   meta.get("transit_time", "TBD"),
        "{{ROUTE}}":          meta.get("route", "Direct"),
        "{{FREQUENCY}}":      meta.get("frequency", "Weekly"),
        "{{EXCHANGE_RATE}}":  f"{meta.get('exchange_rate', 0):.4f}",
        "{{LINE_ITEMS_HEADER}}": _build_items_html(venta.get("line_items", []))[0],
        "{{LINE_ITEMS}}":     _build_items_html(venta.get("line_items", []))[1],
        "{{TOTAL_USD}}":      f"{venta.get('total_usd', 0):,.2f}",
        "{{NOTES}}":          meta.get("notes", ""),
        "{{STAFF_NAME}}":     get_signature(meta.get("staff_code", ""))["name"],
        "{{STAFF_EMAIL}}":    get_signature(meta.get("staff_code", ""))["email"],
        "{{WEIGHT_KG}}":      str(meta.get("weight_kg", "")),
        "{{VOLUME_CBM}}":     str(meta.get("volume_cbm", "")),
    }

    html = template
    for key, value in placeholders.items():
        html = html.replace(key, str(value))
    return html


def generate_pdf(
    venta: dict,
    meta: dict,
    output_path: Path | None = None,
) -> Path:
    """
    Render and write a PDF proforma.
    Raises RuntimeError if WeasyPrint is not installed.
    Returns the path to the written PDF.
    """
    if not WEASYPRINT_AVAILABLE:
        raise RuntimeError(
            "WeasyPrint is not installed. Run: pip install weasyprint"
        )

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    safe_ref = (
        meta.get("reference", "UNKNOWN")
        .replace(" ", "_")
        .replace("/", "-")
        .replace(":", "-")
    )
    out = output_path or (OUTPUT_DIR / f"proforma_{safe_ref}.pdf")

    html_content = render_html(venta, meta)
    _WeasyHTML(string=html_content).write_pdf(str(out))
    return out


def generate_pdf_bytes(venta: dict, meta: dict) -> bytes:
    """Render proforma PDF and return raw bytes (no file written)."""
    if not WEASYPRINT_AVAILABLE:
        raise RuntimeError(
            "WeasyPrint is not installed. Run: pip install weasyprint"
        )
    html_content = render_html(venta, meta)
    return _WeasyHTML(string=html_content).write_pdf()


def generate_html_preview(venta: dict, meta: dict) -> str:
    """Return rendered HTML without writing a PDF (no WeasyPrint required)."""
    return render_html(venta, meta)
