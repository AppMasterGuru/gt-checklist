"""
All Flask routes for the GT Cotizador.

Endpoints:
  GET  /                                  → dashboard
  GET  /quote/new                         → new quote form
  POST /quote/new                         → create quote
  GET  /quote/<ref>                       → quote detail + approval gate
  POST /quote/<ref>/approve               → approve (PENDING → APPROVED)
  POST /quote/<ref>/reject                → reject  (PENDING|APPROVED → REJECTED)
  POST /quote/<ref>/send                  → mark sent (APPROVED → SENT) + fires email stub
  GET  /quote/<ref>/provider-emails       → provider email drafts page
  GET  /quote/<ref>/sintad-export         → SINTAD pre-fill Excel download
  GET  /quote/<ref>/preview.pdf           → inline proforma preview (venta only)
  POST /quote/<ref>/new-version           → copy SENT/REJECTED quote into new PENDING quote
  GET  /audit                             → full audit log with filters
  GET  /audit/export.csv                  → CSV export of audit log
  GET  /api/dashboard/quotes              → JSON quote list for toast polling
  GET  /wca-pilot                         → WCA campaign form
  POST /wca-pilot                         → generate + download campaign ZIP
  POST /api/acknowledgment               → generate ack text (JSON)
  GET  /demo-reset                        → DEV ONLY — clears all test data before demo
  GET  /health                            → health check

APPROVAL GATE RULE: No email is ever sent without explicit human action.
The "send" step marks the record SENT; human sends the actual email (stub until SMTP live).
"""

from __future__ import annotations

import csv
import io
import json
import os
import sqlite3
from datetime import datetime, timedelta, timezone

from flask import (
    Blueprint, Response, flash, jsonify,
    redirect, render_template, request, url_for,
)

from core.acknowledgment import (
    generate_acknowledgment,
    generate_acknowledgment_from_request,
)
from core.provider_status import build_chase_email, compute_provider_statuses
from core.email_listener import process_inbound_emails
from core.whatsapp_listener import process_whatsapp_message
from core.monitor import check_audit_anomalies, generate_daily_digest, run_health_checks
from core.db import audit, get_audit_trail, get_connection, get_provider_replies, get_quote_by_ref, transition_status
from core.email_sender import send_quote_email, send_provider_email, CREDENTIALS_ROTATED
from core.pdf_generator import generate_pdf_bytes, generate_html_preview, WEASYPRINT_AVAILABLE
from core.exchange_rate import get_exchange_rate, soles_to_usd
from core.incoterms import classify_incoterm
from core.provider_emails import generate_provider_emails
from core.reference import generate_reference
from core.sintad_export import generate_sintad_excel
from core.transport import (
    calculate_transport,
    customs_total_usd,
    get_consolidator,
    get_customs_agent,
    visto_bueno_total_usd,
)
from core.units import cbm_from_cm, parse_weight
from core.wca_campaign import generate_wca_campaign, get_pilot_agents
from core.warnings import check_quote_warnings, has_red_warnings
from core.drive import get_air_handling_fee
from procedures import PROCEDURE_VERSION, run_all_checks

bp = Blueprint("cotizador", __name__)

# JP confirmed 20% minimum 2026-05-29 via WhatsApp (overrides Abel's 10%).
# Configurable: change MIN_MARGIN_PCT in .env without code deploy.
MARGIN_FLOOR: float = float(os.getenv("MIN_MARGIN_PCT", "20")) / 100


def _row_to_dict(row: sqlite3.Row) -> dict:
    d = dict(row)
    for field in ("costeo_json", "venta_json", "dimensions_json"):
        raw = d.get(field)
        if raw and isinstance(raw, str):
            try:
                d[field] = json.loads(raw)
            except (json.JSONDecodeError, TypeError):
                pass
    return d


# ── Health ────────────────────────────────────────────────────────────────────

@bp.route("/health")
def health():
    return jsonify({"status": "ok", "ts": datetime.now(timezone.utc).isoformat()})


# ── Listener cron endpoint ────────────────────────────────────────────────────
# Called by Railway cron every 5 minutes (see railway.toml).
# Gate: LISTENER_ENABLED env var must be "true" (case-insensitive).
# If disabled, logs LISTENER_POLL with status=LISTENER_DISABLED and returns
# immediately — no inbox access, no side effects.
# Flip LISTENER_ENABLED=true in Railway env vars to go live; no redeploy needed.

@bp.route("/run-listener", methods=["POST"])
def run_listener():
    ts  = datetime.now(timezone.utc).isoformat()
    enabled = os.environ.get("LISTENER_ENABLED", "false").strip().lower() == "true"

    if not enabled:
        audit("LISTENER_POLL", None, "cron", {"status": "LISTENER_DISABLED", "ts": ts})
        return jsonify({"status": "LISTENER_DISABLED", "ts": ts,
                        "emails_processed": 0, "acks_queued": 0})

    try:
        results      = process_inbound_emails(auto_ack=True)
        n_emails     = len(results)
        n_acks       = sum(1 for r in results if r.get("ack_queued"))
        audit("LISTENER_POLL", None, "cron", {
            "status":          "OK",
            "ts":              ts,
            "emails_processed": n_emails,
            "acks_queued":     n_acks,
        })
        return jsonify({"status": "OK", "ts": ts,
                        "emails_processed": n_emails, "acks_queued": n_acks})
    except Exception as exc:
        audit("LISTENER_POLL", None, "cron", {
            "status": "ERROR", "ts": ts, "error": str(exc),
        })
        return jsonify({"status": "ERROR", "ts": ts, "error": str(exc)}), 500


@bp.route("/run-listener-force", methods=["POST"])
def run_listener_force():
    """Force-run the email listener regardless of LISTENER_ENABLED."""
    ts = datetime.now(timezone.utc).isoformat()
    try:
        results  = process_inbound_emails(auto_ack=True)
        n_emails = len(results)
        n_acks   = sum(1 for r in results if r.get("ack_queued"))
        audit("LISTENER_POLL", None, "run-listener-force", {
            "status": "OK", "ts": ts,
            "emails_processed": n_emails, "acks_queued": n_acks,
            "forced": True,
        })
        return jsonify({"status": "OK", "ts": ts,
                        "emails_processed": n_emails, "acks_queued": n_acks,
                        "forced": True, "results": results})
    except Exception as exc:
        audit("LISTENER_POLL", None, "run-listener-force",
              {"status": "ERROR", "ts": ts, "error": str(exc), "forced": True})
        return jsonify({"status": "ERROR", "ts": ts, "error": str(exc)}), 500


# ── Dashboard ─────────────────────────────────────────────────────────────────

@bp.route("/")
def dashboard():
    with get_connection() as conn:
        rows = conn.execute(
            "SELECT * FROM quotes ORDER BY created_at DESC LIMIT 200"
        ).fetchall()
    quotes = [_row_to_dict(r) for r in rows]
    return render_template("dashboard.html", quotes=quotes)


# ── Dashboard quotes API ──────────────────────────────────────────────────────

@bp.route("/api/dashboard/quotes")
def api_dashboard_quotes():
    """JSON mirror of the dashboard quote list — used for toast polling."""
    with get_connection() as conn:
        rows = conn.execute(
            "SELECT id, reference_code, client_name, status, created_at "
            "FROM quotes ORDER BY created_at DESC LIMIT 200"
        ).fetchall()
    return jsonify([dict(r) for r in rows])


# ── New quote ─────────────────────────────────────────────────────────────────

@bp.route("/quote/new", methods=["GET"])
def new_quote_form():
    return render_template("new_quote.html")


def _auto_send_provider_emails(ref: str, quote: dict, actor: str) -> None:
    """
    Send provider rate-request emails immediately after quote creation.
    Skips if SMTP credentials are not configured; never blocks quote creation.
    """
    try:
        if not CREDENTIALS_ROTATED:
            audit("PROVIDER_EMAILS_SKIPPED", ref, "system", {
                "reason": "graph credentials not configured",
                "mode":   quote.get("mode"),
            })
            return

        emails       = generate_provider_emails(quote)
        sent_count   = 0
        failed_count = 0
        skipped      = 0

        for email in emails:
            to_list = email.get("to_emails") or []
            if not to_list:
                skipped += 1
                continue
            for to_addr in to_list:
                ok, _ = send_provider_email(
                    ref_code=ref,
                    provider=email["provider"],
                    to=to_addr,
                    subject=email["subject"],
                    body=email["body"],
                    actor=actor,
                )
                if ok:
                    sent_count += 1
                else:
                    failed_count += 1

        audit("PROVIDER_EMAILS_AUTO_SENT", ref, actor, {
            "sent":    sent_count,
            "failed":  failed_count,
            "skipped": skipped,
            "mode":    quote.get("mode"),
        })

    except Exception as exc:  # noqa: BLE001
        audit("PROVIDER_EMAILS_AUTO_FAILED", ref, "system", {"error": str(exc)})


@bp.route("/quote/new", methods=["POST"])
def create_quote():
    f = request.form

    client_name  = f.get("client_name", "").strip()
    client_email = f.get("client_email", "").strip()
    incoterm     = f.get("incoterm", "FOB").strip().upper()
    mode         = f.get("mode", "lcl").strip().lower()
    origin       = f.get("origin", "").strip()
    destination  = f.get("destination", "").strip()
    cargo_desc   = f.get("cargo_description", "").strip()
    staff_code   = f.get("staff_code", "GT-PC").strip()
    language     = f.get("language", "es").strip().lower()

    # Weight
    weight_raw  = float(f.get("weight", 0) or 0)
    weight_unit = f.get("weight_unit", "kg")
    weight_kg   = (
        parse_weight(weight_raw, weight_unit) if weight_raw else 0.0
    )

    # Dimensions → CBM
    length_cm = float(f.get("length_cm", 0) or 0)
    width_cm  = float(f.get("width_cm",  0) or 0)
    height_cm = float(f.get("height_cm", 0) or 0)
    quantity  = int(f.get("quantity", 1) or 1)
    cbm = (
        cbm_from_cm(length_cm, width_cm, height_cm, quantity)
        if length_cm
        else float(f.get("volume_cbm", 0) or 0)
    )

    # W/M factor — always computed for LCL (stored in costeo; used by SINTAD + TN/M3 display)
    wm_factor = 0.0
    flete_factor_unit = ""
    if mode == "lcl" and (cbm or weight_kg):
        _vol    = cbm or 0.0
        _wt_ton = (weight_kg or 0.0) / 1000
        wm_factor         = round(max(_vol, _wt_ton), 4)
        flete_factor_unit = "m³" if _vol >= _wt_ton else "ton"

    flete_rate_lcl = float(f.get("flete_rate_lcl") or 0)  # optional USD per W/M
    if mode == "lcl" and flete_rate_lcl and wm_factor:
        flete_usd = round(flete_rate_lcl * wm_factor, 2)
    else:
        flete_rate_lcl = 0.0
        flete_usd      = float(f.get("flete_lcl") or f.get("flete_usd") or 0)

    # THC — optional, from consolidator rate card (per W/M with minimum floor)
    thc_rate = float(f.get("thc_rate") or 0)
    thc_min  = float(f.get("thc_min") or 0)
    thc_usd  = 0.0
    if mode == "lcl" and thc_rate and wm_factor:
        thc_usd = round(max(thc_rate * wm_factor, thc_min), 2)
    consolidator_name  = f.get("consolidator", "MSL").upper()
    airline            = f.get("airline", "").strip()   # aereo only
    requires_oea_basc  = f.get("requires_oea_basc") == "on"
    margin_pct_input   = float(f.get("margin_pct", 20) or 20) / 100
    margin_pct         = max(margin_pct_input, MARGIN_FLOOR)
    requester_type_raw = f.get("requester_type", "cliente").strip()
    requester_type     = requester_type_raw if requester_type_raw in ("cliente", "agente") else "cliente"

    exchange_rate = get_exchange_rate()

    # Transport (weight vs volume — Abel's rule)
    transport_soles = 0.0
    transport_result = {}
    if weight_kg or cbm:
        transport_result = calculate_transport(weight_kg, cbm)
        transport_soles  = transport_result["charge_soles"]
    transport_usd = soles_to_usd(transport_soles, exchange_rate)

    # Customs agent
    agent       = get_customs_agent(requires_oea_basc)
    customs_usd = customs_total_usd(agent)

    # Visto bueno (LCL only)
    vb_usd = 0.0
    consolidator_info = {}
    if mode == "lcl":
        try:
            consolidator_info = get_consolidator(consolidator_name)
            vb_usd = visto_bueno_total_usd(consolidator_info)
        except ValueError:
            flash(f"Unknown consolidator '{consolidator_name}' — defaulting to no visto bueno.", "warning")

    # Air handling fee (aereo only) — looked up from HANDLING AEREO.xlsx
    handling_aereo_usd = 0.0
    handling_aereo_info: dict = {}
    if mode == "aereo" and airline:
        fee = get_air_handling_fee(airline)
        if fee:
            handling_aereo_usd = fee["net_usd"]
            handling_aereo_info = fee

    costeo_total = flete_usd + vb_usd + customs_usd + transport_usd + handling_aereo_usd + thc_usd

    costeo = {
        "flete_internacional_usd": flete_usd,
        "flete_rate_lcl":   flete_rate_lcl   if flete_rate_lcl   else None,
        "flete_factor":     wm_factor         if wm_factor        else None,
        "flete_factor_unit": flete_factor_unit if flete_factor_unit else None,
        "thc_rate":         thc_rate          if thc_rate         else None,
        "thc_usd":          thc_usd           if thc_usd          else None,
        "thc_min":          thc_min           if thc_min          else None,
        "visto_bueno_usd": vb_usd,
        "handling_aereo_usd": handling_aereo_usd,
        "handling_aereo_detail": handling_aereo_info,
        "customs_agent_usd": customs_usd,
        "transport_usd": transport_usd,
        "transport_soles": transport_soles,
        "transport_detail": transport_result,
        "total_usd": round(costeo_total, 2),
        "exchange_rate": exchange_rate,
        "consolidator": consolidator_name if mode == "lcl" else None,
        "airline": airline if mode == "aereo" else None,
        "customs_agent": agent["name"],
    }

    m = 1 + margin_pct
    handling_fees_costeo = vb_usd + customs_usd + handling_aereo_usd

    if mode == "lcl" and flete_rate_lcl:
        flete_item: dict = {
            "description": "International Freight",
            "unit_rate": round(flete_rate_lcl * m, 2),
            "factor_value": wm_factor,
            "factor_unit": flete_factor_unit,  # "m³" or "ton"
            "total": round(flete_usd * m, 2),
        }
    else:
        flete_item = {
            "description": "International Freight",
            "quantity": 1,
            "unit_price": round(flete_usd * m, 2),
            "total": round(flete_usd * m, 2),
        }

    venta_items = [
        flete_item,
        {
            "description": "Handling & Port Fees",
            "quantity": 1,
            "unit_price": round(handling_fees_costeo * m, 2),
            "total": round(handling_fees_costeo * m, 2),
        },
        {
            "description": "Local Transport",
            "quantity": 1,
            "unit_price": round(transport_usd * m, 2),
            "total": round(transport_usd * m, 2),
        },
    ]

    if thc_usd:
        if flete_rate_lcl:
            venta_items.append({
                "description": "THC / Terminal Handling",
                "unit_rate": round(thc_rate * m, 2),
                "factor_value": wm_factor,
                "factor_unit": flete_factor_unit,
                "total": round(thc_usd * m, 2),
            })
        else:
            venta_items.append({
                "description": "THC / Terminal Handling",
                "quantity": 1,
                "unit_price": round(thc_usd * m, 2),
                "total": round(thc_usd * m, 2),
            })

    venta_total = round(sum(item["total"] for item in venta_items), 2)

    venta = {
        "line_items": venta_items,
        "total_usd": venta_total,
        "margin_pct": margin_pct,
        "validity_days": 15,
    }

    with get_connection() as conn:
        ref = generate_reference(conn, client_name, incoterm, staff_code)
        conn.execute(
            """
            INSERT INTO quotes
              (reference_code, client_name, client_email, incoterm, mode, origin, destination,
               cargo_description, weight_kg, volume_cbm, dimensions_json,
               costeo_json, venta_json, margin_pct, exchange_rate,
               status, staff_code, language, requester_type)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,'PENDING',?,?,?)
            """,
            (
                ref, client_name, client_email or None, incoterm, mode, origin, destination,
                cargo_desc, weight_kg, cbm,
                json.dumps({"l": length_cm, "w": width_cm, "h": height_cm, "qty": quantity}),
                json.dumps(costeo),
                json.dumps(venta),
                margin_pct,
                exchange_rate,
                staff_code,
                language,
                requester_type,
            ),
        )
        conn.commit()

    # Run ISO 9001 procedure checks and log any violations (non-blocking)
    procedure_violations = run_all_checks(
        margin_pct=margin_pct,
        mode=mode,
        incoterm=incoterm,
        cargo_description=cargo_desc,
        client_name=client_name,
        weight_kg=weight_kg,
        volume_cbm=cbm,
        consolidator=consolidator_name if mode == "lcl" else None,
    )

    audit("QUOTE_CREATED", ref, staff_code, {
        "client": client_name,
        "mode": mode,
        "incoterm": incoterm,
        "costeo_total_usd": round(costeo_total, 2),
        "venta_total_usd": round(venta_total, 2),
        "margin_pct": margin_pct,
        "procedure_version": PROCEDURE_VERSION,
        "procedure_violations": procedure_violations,
    })

    _auto_send_provider_emails(ref, {
        "reference_code":    ref,
        "mode":              mode,
        "origin":            origin,
        "destination":       destination,
        "incoterm":          incoterm,
        "cargo_description": cargo_desc,
        "weight_kg":         weight_kg,
        "volume_cbm":        cbm,
        "dimensions_json":   {"l": length_cm, "w": width_cm, "h": height_cm, "qty": quantity},
        "staff_code":        staff_code,
        "language":          language,
    }, staff_code)

    flash(f"Cotización {ref} creada y pendiente de aprobación.", "success")
    return redirect(url_for("cotizador.quote_detail", ref_code=ref))


# ── Quote detail + approval gate ──────────────────────────────────────────────

@bp.route("/quote/<path:ref_code>")
def quote_detail(ref_code: str):
    with get_connection() as conn:
        row = conn.execute(
            "SELECT * FROM quotes WHERE reference_code = ?", (ref_code,)
        ).fetchone()

    if row is None:
        flash("Cotización no encontrada.", "error")
        return redirect(url_for("cotizador.dashboard"))

    quote     = _row_to_dict(row)
    audit_log = get_audit_trail(ref_code)
    warnings  = check_quote_warnings(quote)

    emails    = generate_provider_emails(quote)
    expected  = [e["provider"] for e in emails]
    replies   = get_provider_replies(ref_code)
    provider_statuses = compute_provider_statuses(expected, audit_log, replies)
    for ps in provider_statuses:
        if ps["status"] == "red":
            ps["chase"] = build_chase_email(ps["provider"], quote)

    return render_template(
        "quote_detail.html",
        quote=quote,
        audit_log=audit_log,
        warnings=warnings,
        has_red=has_red_warnings(warnings),
        credentials_rotated=CREDENTIALS_ROTATED,
        min_margin_pct=MARGIN_FLOOR,
        provider_statuses=provider_statuses,
    )


@bp.route("/quote/<path:ref_code>/approve", methods=["POST"])
def approve_quote(ref_code: str):
    actor = request.form.get("actor", "approver").strip()
    notes = request.form.get("notes", "").strip()

    with get_connection() as conn:
        row = conn.execute(
            "SELECT id, margin_pct, costeo_json, venta_json FROM quotes WHERE reference_code = ?",
            (ref_code,),
        ).fetchone()
    if row is None:
        flash("Cotización no encontrada.", "error")
        return redirect(url_for("cotizador.dashboard"))

    quote_id = row["id"]

    # ── Margin override (Fix 4+5) ─────────────────────────────────────────────
    override_raw = request.form.get("override_margin_pct", "").strip()
    if override_raw:
        try:
            new_margin = round(float(override_raw) / 100, 6)
            old_margin = float(row["margin_pct"] or 0)
            if abs(new_margin - old_margin) > 0.0001:
                venta = json.loads(row["venta_json"] or "{}")
                new_factor = (1 + new_margin) / (1 + old_margin) if (1 + old_margin) else 1
                line_items = venta.get("line_items") or []
                for item in line_items:
                    for key in ("unit_price", "total", "unit_rate"):
                        if item.get(key) is not None:
                            item[key] = round(item[key] * new_factor, 2)
                venta["line_items"] = line_items
                new_sell_price = round(sum(i.get("total", 0) for i in line_items), 2)
                venta["total_usd"]  = new_sell_price
                venta["margin_pct"] = new_margin
                costeo = json.loads(row["costeo_json"] or "{}")
                costeo_total = float(costeo.get("total_usd", 0))
                with get_connection() as conn:
                    conn.execute(
                        "UPDATE quotes SET margin_pct=?, venta_json=? WHERE id=?",
                        (new_margin, json.dumps(venta), quote_id),
                    )
                    conn.commit()
                audit("MARGIN_OVERRIDE", ref_code, actor, {
                    "from_pct": round(old_margin * 100, 2),
                    "to_pct":   round(new_margin * 100, 2),
                    "new_sell_price_usd": new_sell_price,
                    "costeo_total_usd":   costeo_total,
                })
        except (ValueError, TypeError):
            pass  # Malformed input — proceed with stored margin

    try:
        transition_status(quote_id, "APPROVED", actor, notes)
        flash(f"Cotización {ref_code} aprobada.", "success")
    except Exception as exc:
        flash(f"No se pudo aprobar: {exc}", "error")
    return redirect(url_for("cotizador.quote_detail", ref_code=ref_code))


@bp.route("/quote/<path:ref_code>/reject", methods=["POST"])
def reject_quote(ref_code: str):
    actor = request.form.get("actor", "reviewer").strip()
    notes = request.form.get("notes", "").strip()
    with get_connection() as conn:
        row = conn.execute(
            "SELECT id FROM quotes WHERE reference_code = ?", (ref_code,)
        ).fetchone()
    if row is None:
        flash("Cotización no encontrada.", "error")
        return redirect(url_for("cotizador.dashboard"))
    try:
        transition_status(row["id"], "REJECTED", actor, notes)
        flash(f"Cotización {ref_code} rechazada.", "success")
    except Exception as exc:
        flash(f"No se pudo rechazar: {exc}", "error")
    return redirect(url_for("cotizador.quote_detail", ref_code=ref_code))


@bp.route("/quote/<path:ref_code>/send", methods=["POST"])
def mark_sent(ref_code: str):
    """
    Human confirms the send action.
    1. Transitions quote APPROVED → SENT (DB trigger enforces validity).
    2. Fires send_quote_email() stub — logs QUOTE_SENT to audit trail.
    Real SMTP activates automatically once GT_EMAIL_ADDRESS/PASSWORD are in .env.
    """
    actor = request.form.get("actor", "sender").strip()
    # recipient_email field allows send-time override (e.g. when client_email is blank)
    recipient_override = request.form.get("recipient_email", "").strip()
    with get_connection() as conn:
        row = conn.execute(
            "SELECT id, client_name, client_email, origin, destination, "
            "staff_code, costeo_json, venta_json, language "
            "FROM quotes WHERE reference_code = ?",
            (ref_code,),
        ).fetchone()
    if row is None:
        flash("Cotización no encontrada.", "error")
        return redirect(url_for("cotizador.dashboard"))
    try:
        transition_status(row["id"], "SENT", actor)
    except Exception as exc:
        flash(f"No se pudo marcar como enviada: {exc}", "error")
        return redirect(url_for("cotizador.quote_detail", ref_code=ref_code))

    customer_email = (recipient_override or row["client_email"] or "").strip()
    customer_name  = (row["client_name"]  or "").strip()

    if customer_email:
        # Generate PDF bytes for attachment (Fix 2)
        pdf_bytes: bytes | None = None
        if WEASYPRINT_AVAILABLE:
            try:
                venta  = json.loads(row["venta_json"]  or "{}")
                costeo = json.loads(row["costeo_json"] or "{}")
                meta = {
                    "reference":    ref_code,
                    "client_name":  customer_name,
                    "origin":       row["origin"]      or "",
                    "destination":  row["destination"] or "",
                    "staff_code":   row["staff_code"]  or "",
                    "language":     row["language"]    or "es",
                    "mode":         costeo.get("mode", "lcl"),
                    "consolidator": costeo.get("consolidator", ""),
                    "airline":      costeo.get("airline", ""),
                    "exchange_rate": costeo.get("exchange_rate", 0),
                }
                pdf_bytes = generate_pdf_bytes(venta, meta)
            except Exception:
                pdf_bytes = None  # Send without attachment rather than fail

        ok, msg = send_quote_email(
            ref_code=ref_code,
            quote_id=row["id"],
            customer_email=customer_email,
            customer_name=customer_name,
            actor=actor,
            pdf_bytes=pdf_bytes,
            origin=row["origin"]      or "",
            destination=row["destination"] or "",
            staff_code=row["staff_code"]   or "",
        )
        flash(msg, "success" if ok else "error")
    else:
        flash(
            f"Cotización {ref_code} marcada como enviada. "
            f"Sin email de cliente — envíe manualmente.",
            "success",
        )
    return redirect(url_for("cotizador.quote_detail", ref_code=ref_code))


# ── Provider emails ───────────────────────────────────────────────────────────

@bp.route("/quote/<path:ref_code>/provider-emails")
def provider_emails_page(ref_code: str):
    with get_connection() as conn:
        row = conn.execute(
            "SELECT * FROM quotes WHERE reference_code = ?", (ref_code,)
        ).fetchone()
    if row is None:
        flash("Cotización no encontrada.", "error")
        return redirect(url_for("cotizador.dashboard"))
    quote     = _row_to_dict(row)
    emails    = generate_provider_emails(quote)
    demo_mode = os.environ.get("FLASK_ENV", "production") == "development"
    audit("PROVIDER_EMAILS_GENERATED", ref_code, "system",
          {"count": len(emails), "mode": quote.get("mode")})
    return render_template("provider_emails.html",
                           quote=quote, emails=emails, demo_mode=demo_mode)


@bp.route("/quote/<path:ref_code>/provider-emails/send", methods=["POST"])
def provider_emails_send(ref_code: str):
    """Send all provider rate-request emails for a quote via Graph API."""
    with get_connection() as conn:
        row = conn.execute(
            "SELECT * FROM quotes WHERE reference_code = ?", (ref_code,)
        ).fetchone()
    if row is None:
        flash("Cotización no encontrada.", "error")
        return redirect(url_for("cotizador.dashboard"))

    quote  = _row_to_dict(row)
    emails = generate_provider_emails(quote)
    actor  = quote.get("staff_code") or "abel"

    sent_count   = 0
    failed_count = 0
    skipped      = 0

    for email in emails:
        to_list = email.get("to_emails") or []
        if not to_list:
            skipped += 1
            continue
        for to_addr in to_list:
            ok, msg = send_provider_email(
                ref_code=ref_code,
                provider=email["provider"],
                to=to_addr,
                subject=email["subject"],
                body=email["body"],
                actor=actor,
            )
            if ok:
                sent_count += 1
            else:
                failed_count += 1

    audit("PROVIDER_EMAILS_SEND_ALL", ref_code, actor, {
        "sent":    sent_count,
        "failed":  failed_count,
        "skipped": skipped,
        "mode":    quote.get("mode"),
    })

    if failed_count:
        flash(
            f"Enviados: {sent_count} · Fallidos: {failed_count} · Sin dirección: {skipped}",
            "warning",
        )
    elif sent_count:
        flash(
            f"{sent_count} correo(s) enviado(s) a proveedores. "
            f"Sin dirección registrada: {skipped}",
            "success",
        )
    else:
        flash(
            "Ningún proveedor tiene dirección registrada. "
            "Cargue DATA COLOADERS.xlsx para habilitar el envío.",
            "warning",
        )

    return redirect(url_for("cotizador.provider_emails_page", ref_code=ref_code))


# ── SINTAD Excel export ───────────────────────────────────────────────────────

@bp.route("/quote/<path:ref_code>/sintad-export")
def sintad_export(ref_code: str):
    with get_connection() as conn:
        row = conn.execute(
            "SELECT * FROM quotes WHERE reference_code = ?", (ref_code,)
        ).fetchone()
    if row is None:
        flash("Cotización no encontrada.", "error")
        return redirect(url_for("cotizador.dashboard"))
    quote = _row_to_dict(row)
    if quote.get("status") not in ("APPROVED", "SENT"):
        flash("Solo se puede exportar a SINTAD cotizaciones aprobadas o enviadas.", "error")
        return redirect(url_for("cotizador.quote_detail", ref_code=ref_code))
    xlsx_bytes = generate_sintad_excel(quote)
    audit("SINTAD_EXPORT_GENERATED", ref_code, "system",
          {"status": quote.get("status")})
    safe_ref = ref_code.replace(" ", "_").replace("/", "-")
    return Response(
        xlsx_bytes,
        mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f"attachment; filename=SINTAD_{safe_ref}.xlsx"},
    )



# ── PDF preview (inline on approval gate) ─────────────────────────────────────

@bp.route("/quote/<path:ref_code>/preview.pdf")
def preview_pdf(ref_code: str):
    """Client-facing proforma preview — PDF if WeasyPrint available, else HTML."""
    with get_connection() as conn:
        row = conn.execute(
            "SELECT costeo_json, venta_json, client_name, origin, destination, "
            "staff_code, language FROM quotes WHERE reference_code = ?",
            (ref_code,),
        ).fetchone()
    if row is None:
        return Response("Not found", status=404)

    venta  = json.loads(row["venta_json"]  or "{}")
    costeo = json.loads(row["costeo_json"] or "{}")

    override_raw = request.args.get("override_margin_pct", "").strip()
    if override_raw:
        try:
            new_margin = float(override_raw) / 100
            old_margin = float(venta.get("margin_pct") or 0)
            venta = dict(venta)
            venta["line_items"] = [dict(i) for i in (venta.get("line_items") or [])]
            if abs(new_margin - old_margin) > 0.0001 and (1 + old_margin):
                new_factor = (1 + new_margin) / (1 + old_margin)
                for item in venta["line_items"]:
                    for key in ("unit_price", "total", "unit_rate"):
                        if item.get(key) is not None:
                            item[key] = round(item[key] * new_factor, 2)
            venta["total_usd"]  = round(sum(i.get("total", 0) for i in venta["line_items"]), 2)
            venta["margin_pct"] = new_margin
        except (ValueError, TypeError):
            pass

    meta = {
        "reference":    ref_code,
        "client_name":  row["client_name"] or "",
        "origin":       row["origin"]      or "",
        "destination":  row["destination"] or "",
        "staff_code":   row["staff_code"]  or "",
        "language":     row["language"]    or "es",
        "mode":         costeo.get("mode", "lcl"),
        "consolidator": costeo.get("consolidator", ""),
        "airline":      costeo.get("airline", ""),
        "exchange_rate": costeo.get("exchange_rate", 0),
    }

    if WEASYPRINT_AVAILABLE:
        try:
            pdf_bytes = generate_pdf_bytes(venta, meta)
            return Response(
                pdf_bytes, mimetype="application/pdf",
                headers={"Content-Disposition": "inline"},
            )
        except Exception:
            pass

    html = generate_html_preview(venta, meta)
    return Response(html, mimetype="text/html")


# ── New version (copy SENT/REJECTED → new PENDING) ────────────────────────────

@bp.route("/quote/<path:ref_code>/new-version", methods=["POST"])
def new_version(ref_code: str):
    """Clone a SENT or REJECTED quote into a new PENDING quote."""
    actor = request.form.get("actor", "").strip()
    with get_connection() as conn:
        row = conn.execute(
            "SELECT client_name, client_email, incoterm, mode, origin, destination, "
            "cargo_description, weight_kg, volume_cbm, dimensions_json, "
            "costeo_json, venta_json, margin_pct, exchange_rate, staff_code, language "
            "FROM quotes WHERE reference_code = ? AND status IN ('SENT','REJECTED')",
            (ref_code,),
        ).fetchone()
    if row is None:
        flash("Solo se pueden versionar cotizaciones enviadas o rechazadas.", "error")
        return redirect(url_for("cotizador.dashboard"))

    with get_connection() as conn:
        new_ref = generate_reference(conn, row["client_name"], row["incoterm"], row["staff_code"])
        conn.execute(
            """
            INSERT INTO quotes
              (reference_code, client_name, client_email, incoterm, mode, origin, destination,
               cargo_description, weight_kg, volume_cbm, dimensions_json,
               costeo_json, venta_json, margin_pct, exchange_rate,
               status, staff_code, language)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,'PENDING',?,?)
            """,
            (
                new_ref,
                row["client_name"], row["client_email"], row["incoterm"],
                row["mode"], row["origin"], row["destination"],
                row["cargo_description"], row["weight_kg"], row["volume_cbm"],
                row["dimensions_json"], row["costeo_json"], row["venta_json"],
                row["margin_pct"], row["exchange_rate"],
                row["staff_code"], row["language"],
            ),
        )
        conn.commit()

    audit("QUOTE_VERSION_CREATED", new_ref, actor or "system", {
        "original_ref": ref_code,
        "new_ref": new_ref,
    })
    flash(f"Nueva versión {new_ref} creada a partir de {ref_code}.", "success")
    return redirect(url_for("cotizador.quote_detail", ref_code=new_ref))


# ── Audit log page ────────────────────────────────────────────────────────────

@bp.route("/audit")
def audit_log_page():
    q_ref    = request.args.get("quote_ref", "").strip()
    actor_f  = request.args.get("actor", "").strip()
    action_f = request.args.get("action", "").strip()
    from_dt  = request.args.get("from", "").strip()
    to_dt    = request.args.get("to", "").strip()

    sql    = "SELECT * FROM audit_log WHERE 1=1"
    params: list = []
    if q_ref:
        sql += " AND quote_reference LIKE ?"
        params.append(f"%{q_ref}%")
    if actor_f:
        sql += " AND actor LIKE ?"
        params.append(f"%{actor_f}%")
    if action_f:
        sql += " AND event_type LIKE ?"
        params.append(f"%{action_f}%")
    if from_dt:
        sql += " AND ts >= ?"
        params.append(from_dt)
    if to_dt:
        sql += " AND ts <= ?"
        params.append(to_dt + "T23:59:59")
    sql += " ORDER BY ts DESC LIMIT 500"

    with get_connection() as conn:
        rows = conn.execute(sql, params).fetchall()
    entries = [dict(r) for r in rows]
    return render_template("audit.html", entries=entries, filters={
        "quote_ref": q_ref, "actor": actor_f, "action": action_f,
        "from": from_dt, "to": to_dt,
    })


@bp.route("/audit/export.csv")
def audit_export_csv():
    with get_connection() as conn:
        rows = conn.execute(
            "SELECT * FROM audit_log ORDER BY ts DESC"
        ).fetchall()
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(["id", "ts", "event_type", "quote_reference", "actor", "detail_json"])
    for row in rows:
        writer.writerow([
            row["id"], row["ts"], row["event_type"],
            row["quote_reference"], row["actor"], row["detail_json"],
        ])
    return Response(
        buf.getvalue(),
        mimetype="text/csv",
        headers={"Content-Disposition": "attachment; filename=audit_log.csv"},
    )


# ── WCA pilot campaign ────────────────────────────────────────────────────────

_WCA_VERTICALS = [
    ("perecibles",   "Perecibles / Cadena de frío"),
    ("minería",      "Minería / Maquinaria pesada"),
    ("textiles",     "Textiles / Confecciones"),
    ("electrónica",  "Electrónica / Alto valor"),
    ("farmacéutico", "Farmacéutico / Salud"),
    ("general",      "Carga general / LCL"),
]

@bp.route("/wca-pilot", methods=["GET", "POST"])
def wca_pilot():
    if request.method == "GET":
        return render_template("wca_pilot.html", verticals=_WCA_VERTICALS)

    country     = request.form.get("country", "").strip()
    vertical    = request.form.get("vertical", "general").strip()
    n_agents    = max(10, min(30, int(request.form.get("n_agents", 15) or 15)))
    language    = request.form.get("language", "es").strip()
    sender_name = request.form.get("sender_name", "Renato Alvarez").strip()

    if not country:
        flash("Debe ingresar un país destino.", "error")
        return render_template("wca_pilot.html", verticals=_WCA_VERTICALS)

    agents = get_pilot_agents(country, vertical) or None

    try:
        zip_bytes = generate_wca_campaign(
            country, vertical, n_agents, language, sender_name, agents=agents
        )
    except ValueError as exc:
        flash(str(exc), "error")
        return render_template("wca_pilot.html", verticals=_WCA_VERTICALS)

    n_actual = len(agents) if agents else n_agents
    audit("WCA_CAMPAIGN_GENERATED", None, sender_name, {
        "country": country, "vertical": vertical,
        "n_agents": n_actual, "language": language,
        "personalised": agents is not None,
    })
    fname = f"wca_{country.lower().replace(' ', '_')}_{vertical}.zip"
    return Response(
        zip_bytes,
        mimetype="application/zip",
        headers={"Content-Disposition": f"attachment; filename={fname}"},
    )


# ── Demo reset (DEV ONLY) ─────────────────────────────────────────────────────
# Clears all data and optionally seeds demo quotes. Guarded by ?password=gt2026.
# Optional: &seed=true — after clearing, pre-loads 3 demo quotes in different states:
#   Quote 1 (LCL Lima→Hamburg, Hamburg Importer GmbH)   → PENDING
#   Quote 2 (Aéreo Lima→LAX,   Miami Foods Corp)         → APPROVED
#   Quote 3 (FCL Manzanillo→Callao, Distribuidora Lima)  → SENT


def _reset_db() -> int:
    """Clear quotes + audit_log. Return count of quotes cleared."""
    with get_connection() as conn:
        n_quotes = conn.execute("SELECT COUNT(*) FROM quotes").fetchone()[0]
        conn.execute("DELETE FROM quotes")
        conn.execute("DELETE FROM sqlite_sequence WHERE name='quotes'")
        # audit_log has a BEFORE DELETE trigger. For dev reset we drop + recreate.
        conn.execute("DROP TABLE audit_log")
        conn.execute("""
            CREATE TABLE audit_log (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                event_type      TEXT    NOT NULL,
                quote_reference TEXT,
                actor           TEXT,
                detail_json     TEXT,
                ts              TEXT    NOT NULL DEFAULT (datetime('now'))
            )
        """)
        conn.execute("""
            CREATE TRIGGER protect_audit_log_update
            BEFORE UPDATE ON audit_log
            BEGIN
                SELECT RAISE(ABORT, 'audit_log is immutable — UPDATE not permitted');
            END
        """)
        conn.execute("""
            CREATE TRIGGER protect_audit_log_delete
            BEFORE DELETE ON audit_log
            BEGIN
                SELECT RAISE(ABORT, 'audit_log is immutable — DELETE not permitted');
            END
        """)
        conn.commit()
    return n_quotes


def _seed_demo_providers() -> int:
    """
    Ensure the providers table has demo-ready contacts for the 5 LCL providers
    so the provider-emails page always shows real To: addresses during demo.
    Uses seed_providers() which skips exact duplicates — safe to call repeatedly.
    If real DATA COLOADERS contacts are already loaded, this adds nothing.
    """
    from core.providers import seed_providers  # noqa: PLC0415

    demo_contacts = [
        {"company": "MSL CORPORATE",  "contact_name": "Franccesco Urrutia",
         "role": "LCL Export Sales",  "email": "furrutia@mslcorporate.com", "phone": ""},
        {"company": "CRAFT",          "contact_name": "Área Comercial",
         "role": "LCL Export Sales",  "email": "comercial@craftlogistics.com", "phone": ""},
        {"company": "SACO",           "contact_name": "Área Comercial",
         "role": "LCL Export Sales",  "email": "ventas@sacocargos.com",        "phone": ""},
        {"company": "VANGUARD",       "contact_name": "Área Comercial",
         "role": "LCL Export Sales",  "email": "sales@vanguardlogistics.com",  "phone": ""},
        {"company": "ECU WORLDWIDE",  "contact_name": "Área Comercial",
         "role": "LCL Export Sales",  "email": "peru@ecuworldwide.com",        "phone": ""},
    ]
    return seed_providers(demo_contacts)


def _seed_demo_provider_statuses(hamburg_ref: str) -> None:
    """
    Backdate provider contact + reply records for the Hamburg Importer demo quote
    so the provider status panel shows all four states simultaneously:
      MSL          contacted 48h ago, reply received → GREEN
      CRAFT        contacted 12h ago, no reply       → ORANGE
      SACO         contacted 36h ago, no reply       → RED (shows Enviar recordatorio)
      VANGUARD     never contacted                   → GREY
      ECU WORLDWIDE never contacted                  → GREY
    """
    now = datetime.now(timezone.utc)
    contacts = [
        ("MSL",   now - timedelta(hours=48)),
        ("CRAFT", now - timedelta(hours=12)),
        ("SACO",  now - timedelta(hours=36)),
    ]
    with get_connection() as conn:
        for provider, ts in contacts:
            conn.execute(
                "INSERT INTO audit_log (event_type, quote_reference, actor, detail_json, ts)"
                " VALUES (?, ?, ?, ?, ?)",
                (
                    "PROVIDER_EMAIL_SENT",
                    hamburg_ref,
                    "demo-seed",
                    json.dumps({"provider": provider, "to": f"rates@{provider.lower().replace(' ', '')}.com"}),
                    ts.isoformat(),
                ),
            )
        # MSL reply: flete 480, visto bueno 160
        conn.execute(
            """INSERT INTO provider_replies
               (quote_reference, provider_name, sender_email, email_subject,
                flete_usd, visto_bueno_usd, transit_days, validity_days,
                parse_status, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                hamburg_ref, "MSL", "rates@msl.com",
                f"RE: {hamburg_ref} — Tarifa LCL Lima → Hamburgo",
                480.0, 160.0, 21, 15, "parsed",
                (now - timedelta(hours=6)).isoformat(),
            ),
        )
        conn.commit()


def _seed_demo_quotes() -> list[str]:
    """
    Pre-load 3 realistic demo quotes in different states.
    Uses the same core functions as create_quote — state machine respected.
    Returns list of reference codes created.
    """
    # Ensure provider emails are available for the provider-emails demo page.
    _seed_demo_providers()

    from core.exchange_rate import get_exchange_rate, soles_to_usd
    from core.reference import generate_reference
    from core.transport import (
        calculate_transport, customs_total_usd,
        get_customs_agent, get_consolidator, visto_bueno_total_usd,
    )
    from core.drive import get_air_handling_fee

    MARGIN_FLOOR_LOCAL = 0.10

    exchange_rate = get_exchange_rate()

    specs = [
        {
            "client_name":       "Hamburg Importer GmbH",
            "client_email":      "import@hamburg-importer.de",
            "incoterm":          "EXW",
            "mode":              "lcl",
            "origin":            "Lima, Perú",
            "destination":       "Hamburgo, Alemania",
            "cargo_description": "Uvas frescas — refrigeradas, perecibles",
            "weight_kg":         850.0,
            "volume_cbm":        3.2,
            "flete_usd":         275.0,
            "consolidator":      "CRAFT",
            "airline":           "",
            "margin_pct":        0.22,
            "staff_code":        "GT-PC",
            "language":          "es",
            "target_status":     "PENDING",
        },
        {
            "client_name":       "Miami Foods Corp",
            "client_email":      "logistics@miamifoods.com",
            "incoterm":          "EXW",
            "mode":              "aereo",
            "origin":            "Lima, Perú",
            "destination":       "Los Angeles, CA, USA",
            "cargo_description": "Espárragos frescos — perecibles, temperatura controlada",
            "weight_kg":         240.0,
            "volume_cbm":        1.1,
            "flete_usd":         1200.0,
            "consolidator":      "",
            "airline":           "LAN Airlines",
            "margin_pct":        0.20,
            "staff_code":        "GT-LOC",
            "language":          "en",
            "target_status":     "APPROVED",
        },
        {
            "client_name":       "Distribuidora Lima SAC",
            "client_email":      "importaciones@distribuidoralima.pe",
            "incoterm":          "DAP",
            "mode":              "fcl",
            "origin":            "Manzanillo, México",
            "destination":       "Callao, Perú",
            "cargo_description": "Maquinaria industrial — 40'HC, carga pesada",
            "weight_kg":         18500.0,
            "volume_cbm":        67.3,
            "flete_usd":         2800.0,
            "consolidator":      "",
            "airline":           "",
            "margin_pct":        0.18,
            "staff_code":        "RENATO",
            "language":          "es",
            "target_status":     "SENT",
        },
    ]

    refs = []

    for spec in specs:
        mode      = spec["mode"]
        flete_usd = spec["flete_usd"]
        weight_kg = spec["weight_kg"]
        cbm       = spec["volume_cbm"]

        transport_result = calculate_transport(weight_kg, cbm)
        transport_soles  = transport_result["charge_soles"]
        transport_usd    = soles_to_usd(transport_soles, exchange_rate)

        agent       = get_customs_agent(False)
        customs_usd = customs_total_usd(agent)

        vb_usd = 0.0
        if mode == "lcl" and spec["consolidator"]:
            try:
                cons   = get_consolidator(spec["consolidator"])
                vb_usd = visto_bueno_total_usd(cons)
            except ValueError:
                pass

        handling_aereo_usd  = 0.0
        handling_aereo_info: dict = {}
        if mode == "aereo" and spec.get("airline"):
            fee = get_air_handling_fee(spec["airline"])
            if fee:
                handling_aereo_usd  = fee["net_usd"]
                handling_aereo_info = fee

        costeo_total = flete_usd + vb_usd + customs_usd + transport_usd + handling_aereo_usd
        margin_pct   = max(spec["margin_pct"], MARGIN_FLOOR_LOCAL)

        # W/M factor for LCL seeds (stored so SINTAD export can populate TN/M3)
        _seed_vol    = cbm or 0.0
        _seed_wt_ton = (weight_kg or 0.0) / 1000
        seed_wm_factor      = round(max(_seed_vol, _seed_wt_ton), 4) if mode == "lcl" else None
        seed_factor_unit    = ("m³" if _seed_vol >= _seed_wt_ton else "ton") if mode == "lcl" else None

        costeo = {
            "flete_internacional_usd": flete_usd,
            "flete_factor":      seed_wm_factor,
            "flete_factor_unit": seed_factor_unit,
            "visto_bueno_usd":         vb_usd,
            "handling_aereo_usd":      handling_aereo_usd,
            "handling_aereo_detail":   handling_aereo_info,
            "customs_agent_usd":       customs_usd,
            "transport_usd":           transport_usd,
            "transport_soles":         transport_soles,
            "transport_detail":        transport_result,
            "total_usd":               round(costeo_total, 2),
            "exchange_rate":           exchange_rate,
            "consolidator":            spec["consolidator"] if mode == "lcl" else None,
            "airline":                 spec["airline"] if mode == "aereo" else None,
            "customs_agent":           agent["name"],
        }

        _m = 1 + margin_pct
        _handling = vb_usd + customs_usd + handling_aereo_usd
        _seed_items = [
            {"description": "International Freight", "quantity": 1,
             "unit_price": round(flete_usd * _m, 2), "total": round(flete_usd * _m, 2)},
            {"description": "Handling & Port Fees",  "quantity": 1,
             "unit_price": round(_handling * _m, 2), "total": round(_handling * _m, 2)},
            {"description": "Local Transport",       "quantity": 1,
             "unit_price": round(transport_usd * _m, 2), "total": round(transport_usd * _m, 2)},
        ]
        venta_total = round(sum(i["total"] for i in _seed_items), 2)
        venta = {
            "line_items": _seed_items,
            "total_usd":    venta_total,
            "margin_pct":   margin_pct,
            "validity_days": 15,
        }

        with get_connection() as conn:
            ref = generate_reference(conn, spec["client_name"], spec["incoterm"], spec["staff_code"])
            result = conn.execute(
                """
                INSERT INTO quotes
                  (reference_code, client_name, client_email, incoterm, mode,
                   origin, destination, cargo_description,
                   weight_kg, volume_cbm, dimensions_json,
                   costeo_json, venta_json, margin_pct, exchange_rate,
                   status, staff_code, language)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,'PENDING',?,?)
                """,
                (
                    ref, spec["client_name"], spec["client_email"],
                    spec["incoterm"], mode, spec["origin"], spec["destination"],
                    spec["cargo_description"], weight_kg, cbm,
                    json.dumps({"l": 0, "w": 0, "h": 0, "qty": 1}),
                    json.dumps(costeo), json.dumps(venta),
                    margin_pct, exchange_rate, spec["staff_code"], spec["language"],
                ),
            )
            conn.commit()
            quote_id = result.lastrowid

        audit("QUOTE_CREATED", ref, "demo-seed", {
            "client":       spec["client_name"],
            "mode":         mode,
            "costeo_total": round(costeo_total, 2),
            "venta_total":  round(venta_total, 2),
            "seeded":       True,
        })

        # Advance to target state via valid transitions
        if spec["target_status"] in ("APPROVED", "SENT"):
            transition_status(quote_id, "APPROVED", "JP")
        if spec["target_status"] == "SENT":
            transition_status(quote_id, "SENT", "demo-seed")

        refs.append(ref)

    # Seed provider contact + reply records for the Hamburg Importer (first ref, PENDING)
    _seed_demo_provider_statuses(refs[0])

    return refs


@bp.route("/demo-reset")
def demo_reset():
    password = request.args.get("password", "")
    seed     = request.args.get("seed", "false").lower() == "true"
    if password != "gt2026":
        return jsonify({"error": "not allowed"}), 403

    n_quotes = _reset_db()
    audit("DEMO_RESET", None, "demo", {
        "quotes_cleared": n_quotes,
        "seed_requested": seed,
        "detail": "Database cleared for demo",
    })

    if seed:
        seeded_refs = _seed_demo_quotes()
        audit("DEMO_SEEDED", None, "demo", {
            "quotes_seeded": len(seeded_refs),
            "references":    seeded_refs,
            "states":        ["PENDING", "APPROVED", "SENT"],
            "detail":        "3 demo quotes pre-loaded in PENDING/APPROVED/SENT states",
        })
        return jsonify({
            "status":         "reset+seeded",
            "quotes_cleared": n_quotes,
            "quotes_seeded":  len(seeded_refs),
            "references":     seeded_refs,
            "states":         ["PENDING", "APPROVED", "SENT"],
            "timestamp":      datetime.now(timezone.utc).isoformat(),
        })

    return jsonify({
        "status":         "reset",
        "quotes_cleared": n_quotes,
        "timestamp":      datetime.now(timezone.utc).isoformat(),
    })


# ── Acknowledgment API ────────────────────────────────────────────────────────

@bp.route("/api/acknowledgment", methods=["POST"])
def acknowledgment_api():
    data = request.get_json(force=True, silent=True) or {}
    text = generate_acknowledgment(
        language=data.get("language", "es"),
        client=data.get("client", ""),
        cargo_summary=data.get("cargo_summary", ""),
        reference=data.get("reference", ""),
        staff_name=data.get("staff_name", "Equipo Comercial"),
        response_hours=int(data.get("response_hours", 4)),
    )
    return jsonify({"text": text, "language": data.get("language", "es")})


@bp.route("/acknowledgment/demo")
def acknowledgment_demo():
    """
    Demo page: shows 3 sample inbound emails (ES, EN, DE) and the acknowledgment
    generated for each. Demonstrates Pipeline #3 Step 1 (multilingual auto-ack).
    Uses stub mode when ANTHROPIC_API_KEY not set — templates are pre-built and demo-ready.
    """
    parsed_emails = process_inbound_emails()
    demos = []
    for parsed in parsed_emails:
        ack = generate_acknowledgment_from_request(parsed)
        demos.append({
            "parsed":  parsed,
            "ack":     ack,
            "subject": ack["subject"],
            "body":    ack["body"],
            "language": ack["language"],
            "topic":   ack["detected_topic"],
        })
    return render_template("acknowledgment_demo.html", demos=demos)


# ── Monitor dashboard ─────────────────────────────────────────────────────────
# Internal — TimeBack AI only. Not shown to GT staff during demo.

@bp.route("/monitor")
def monitor_dashboard():
    """
    Internal monitoring dashboard. Auto-refreshes every 60 seconds.
    Shows live health status, anomalies, and 24h stats.
    TimeBack AI eyes only — not part of client-facing demo.
    """
    health   = run_health_checks()
    anomalies = check_audit_anomalies()
    digest   = generate_daily_digest()

    # Last 10 audit events for recent-activity feed
    with get_connection() as conn:
        recent_rows = conn.execute(
            "SELECT ts, event_type, quote_reference, actor FROM audit_log "
            "ORDER BY ts DESC LIMIT 10"
        ).fetchall()
    recent_events = [dict(r) for r in recent_rows]

    return render_template(
        "monitor.html",
        health=health,
        anomalies=anomalies,
        digest=digest,
        recent_events=recent_events,
    )


# ── Email listener preview ────────────────────────────────────────────────────

@bp.route("/email-listener/preview")
def email_listener_preview():
    """
    Preview page: shows 3 stub inbound emails parsed into structured fields.
    Used for demo and testing — confirms the listener extracts the right data.
    STUB MODE: Uses hardcoded sample emails + keyword-based parse.
    LIVE MODE: Uses real IMAP fetch + Claude API parse.
    """
    parsed_emails = process_inbound_emails()
    return render_template("email_listener_preview.html", parsed_emails=parsed_emails)


# ── WhatsApp webhook ──────────────────────────────────────────────────────────

@bp.route("/webhook/whatsapp", methods=["GET"])
def whatsapp_webhook_verify():
    """
    Meta webhook verification handshake.

    Meta sends GET /webhook/whatsapp?hub.mode=subscribe
                                    &hub.verify_token=<WHATSAPP_VERIFY_TOKEN>
                                    &hub.challenge=<challenge_string>

    We must respond with hub.challenge (plain text, 200) to confirm ownership.
    If verify_token does not match, respond 403.
    """
    hub_mode = request.args.get("hub.mode", "")
    hub_token = request.args.get("hub.verify_token", "")
    hub_challenge = request.args.get("hub.challenge", "")

    expected_token = os.environ.get("WHATSAPP_VERIFY_TOKEN", "")

    if hub_mode == "subscribe" and hub_token == expected_token:
        return Response(hub_challenge, status=200, mimetype="text/plain")

    return Response("Forbidden", status=403, mimetype="text/plain")


# ── Acknowledgment log (acuses) ───────────────────────────────────────────────

@bp.route("/acuses")
def acuses():
    """Acknowledgment send log — lists every auto-ack queued or sent."""
    import json as _json
    from pathlib import Path as _Path

    ack_path = _Path(__file__).parent.parent / "pending_acks.jsonl"
    entries: list[dict] = []
    if ack_path.exists():
        for line in ack_path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line:
                try:
                    entries.append(_json.loads(line))
                except _json.JSONDecodeError:
                    pass
    entries.reverse()  # newest first
    return render_template("acuses.html", entries=entries)


@bp.route("/webhook/whatsapp", methods=["POST"])
def whatsapp_webhook_receive():
    """
    Receive inbound WhatsApp messages from Meta.

    Parses the webhook payload, routes it through the quote pipeline,
    and returns 200 immediately (Meta requires a fast 200 ACK).
    """
    payload = request.get_json(silent=True) or {}
    result = process_whatsapp_message(payload)
    return jsonify({"status": "received", "channel": "whatsapp"}), 200
