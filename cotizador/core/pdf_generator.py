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

_TEMPLATES_DIR = Path(__file__).parent.parent / "templates"
_TEMPLATE_PATH  = _TEMPLATES_DIR / "proforma.html"
OUTPUT_DIR = Path(os.getenv("OUTPUT_DIR", "/tmp/gt_cotizador_output"))

VALIDITY_DAYS = 15  # standard; Abel confirmed


def _template_for_lang(lang: str) -> Path:
    """Return the language-appropriate proforma template path."""
    if lang == "en":
        p = _TEMPLATES_DIR / "proforma_en.html"
    else:
        p = _TEMPLATES_DIR / "proforma_es.html"
    return p if p.exists() else _TEMPLATE_PATH


def _build_flete_table(intl_items: list[dict], lang: str = "es") -> str:
    """HTML table for Section 1: international freight charges (no IGV)."""
    if not intl_items:
        return ""
    has_factor = any(item.get("factor_value") is not None for item in intl_items)
    hdr_label = "Costos de Flete Internacional" if lang == "es" else "International Freight Charges"
    col2 = "Tarifa" if has_factor else "Cant."
    col3 = "TN/M3" if has_factor else ("Precio Unit." if lang == "es" else "Unit Price")
    desc_col = "Concepto" if lang == "es" else "Description"
    sub_lbl  = "Subtotal Flete" if lang == "es" else "Freight Subtotal"

    rows = ""
    for item in intl_items:
        desc  = item.get("description", "")
        total = item.get("total") or 0
        if has_factor and item.get("factor_value") is not None:
            rate  = item.get("unit_rate") or 0
            fval  = item.get("factor_value", 0)
            funit = item.get("factor_unit", "")
            rows += (
                f'<tr><td>{desc}</td>'
                f'<td class="num">USD {rate:,.2f}/W·M</td>'
                f'<td class="num">{fval:.4g} {funit}</td>'
                f'<td class="num">USD {total:,.2f}</td></tr>'
            )
        elif has_factor:
            rows += (
                f'<tr><td>{desc}</td>'
                f'<td class="num">—</td><td class="num">—</td>'
                f'<td class="num">USD {total:,.2f}</td></tr>'
            )
        else:
            qty = item.get("quantity", 1)
            up  = item.get("unit_price", 0)
            rows += (
                f'<tr><td>{desc}</td>'
                f'<td class="num">{qty}</td>'
                f'<td class="num">USD {up:,.2f}</td>'
                f'<td class="num">USD {total:,.2f}</td></tr>'
            )

    subtotal = sum(i.get("total") or 0 for i in intl_items)
    return (
        f'<h3 class="charges-section-hdr">{hdr_label}</h3>'
        f'<table class="charges">'
        f'<thead><tr><th>{desc_col}</th>'
        f'<th class="num">{col2}</th><th class="num">{col3}</th>'
        f'<th class="num">Total (USD)</th></tr></thead>'
        f'<tbody>{rows}</tbody>'
        f'<tfoot><tr><td colspan="3"><strong>{sub_lbl}</strong></td>'
        f'<td class="num"><strong>USD {subtotal:,.2f}</strong></td></tr></tfoot>'
        f'</table>'
    )


def _build_local_table(local_items: list[dict], lang: str = "es") -> str:
    """HTML table for Section 2: local charges with IGV 18% columns."""
    if not local_items:
        return ""
    hdr_label = "Gastos Locales (+ IGV 18%)" if lang == "es" else "Local Charges (incl. VAT 18%)"
    col_neto  = "Monto Neto" if lang == "es" else "Net Amount"
    sub_lbl   = "Subtotal Gastos Locales" if lang == "es" else "Local Subtotal"
    desc_col  = "Concepto" if lang == "es" else "Description"

    rows = ""
    for item in local_items:
        desc  = item.get("description", "")
        neto  = item.get("total") or 0
        rows += (
            f'<tr><td>{desc}</td>'
            f'<td class="num">USD {neto:,.2f}</td>'
            f'<td class="num">USD {neto * 0.18:,.2f}</td>'
            f'<td class="num">USD {neto * 1.18:,.2f}</td></tr>'
        )

    neto_sum = sum(i.get("total") or 0 for i in local_items)
    return (
        f'<h3 class="charges-section-hdr">{hdr_label}</h3>'
        f'<table class="charges">'
        f'<thead><tr><th>{desc_col}</th>'
        f'<th class="num">{col_neto}</th>'
        f'<th class="num">IGV 18%</th>'
        f'<th class="num">Total (USD)</th></tr></thead>'
        f'<tbody>{rows}</tbody>'
        f'<tfoot><tr><td><strong>{sub_lbl}</strong></td>'
        f'<td class="num"><strong>USD {neto_sum:,.2f}</strong></td>'
        f'<td class="num"><strong>USD {neto_sum * 0.18:,.2f}</strong></td>'
        f'<td class="num"><strong>USD {neto_sum * 1.18:,.2f}</strong></td></tr></tfoot>'
        f'</table>'
    )


def render_html(venta: dict, meta: dict) -> str:
    """
    Render the proforma HTML from venta data and metadata.
    venta: sell-side breakdown (never costeo)
    meta:  reference, client, origin, destination, incoterm, mode, staff info
    """
    lang = (meta.get("language") or "es").lower()
    template = _template_for_lang(lang).read_text(encoding="utf-8")

    today         = date.today()
    validity_date = today + timedelta(days=VALIDITY_DAYS)

    line_items  = venta.get("line_items", [])
    local_items = [i for i in line_items if i.get("is_local")]
    intl_items  = [i for i in line_items if not i.get("is_local")]

    intl_subtotal = sum(i.get("total") or 0 for i in intl_items)
    local_neto    = sum(i.get("total") or 0 for i in local_items)
    grand_total   = intl_subtotal + local_neto * 1.18 if local_items else intl_subtotal + local_neto

    sig = get_signature(meta.get("staff_code", ""))
    placeholders = {
        "{{REFERENCE}}":     meta.get("reference", ""),
        "{{CLIENT_NAME}}":   meta.get("client_name", ""),
        "{{ORIGIN}}":        meta.get("origin", ""),
        "{{DESTINATION}}":   meta.get("destination", ""),
        "{{INCOTERM}}":      meta.get("incoterm", ""),
        "{{MODE}}":          meta.get("mode", "").upper(),
        "{{DATE}}":          today.strftime("%d/%m/%Y"),
        "{{VALIDITY_DATE}}": validity_date.strftime("%d/%m/%Y"),
        "{{VALIDITY_DAYS}}": str(VALIDITY_DAYS),
        "{{TRANSIT_TIME}}":  meta.get("transit_time", "TBD"),
        "{{ROUTE}}":         meta.get("route", "Direct" if lang == "en" else "Directa"),
        "{{FREQUENCY}}":     meta.get("frequency", "Weekly" if lang == "en" else "Semanal"),
        "{{EXCHANGE_RATE}}": f"{meta.get('exchange_rate', 0):.4f}",
        "{{FLETE_TABLE}}":   _build_flete_table(intl_items, lang),
        "{{LOCAL_TABLE}}":   _build_local_table(local_items, lang),
        "{{TOTAL_USD}}":     f"{grand_total:,.2f}",
        "{{NOTES}}":         meta.get("notes", ""),
        "{{STAFF_NAME}}":    sig["name"],
        "{{STAFF_EMAIL}}":   sig["email"],
        "{{WEIGHT_KG}}":     str(meta.get("weight_kg", "")),
        "{{VOLUME_CBM}}":    str(meta.get("volume_cbm", "")),
        # Legacy placeholders kept for proforma_es/en templates
        "{{DOC_TITLE}}":     meta.get("doc_title", ""),
        "{{CARGO_TABLE}}":   meta.get("cargo_table_html", ""),
        "{{ROUTE_TABLE}}":   meta.get("route_table_html", ""),
        "{{NOTES_SECTION}}": meta.get("notes_section_html", ""),
        "{{HEADER_IMG_SRC}}": meta.get("header_img_src", ""),
        "{{FOOTER_IMG_SRC}}": meta.get("footer_img_src", ""),
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
