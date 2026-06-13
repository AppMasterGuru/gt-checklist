# CLAUDE.md — Global Transport Cotizador
**Client:** Global Transport SAC  
**Project:** Pipeline #1 — Cotizador (automated quoting system)  
**Engagement lead:** Barney Elliott / TimeBack AI  
**Last updated:** 2026-06-13

---

## What we are building

A full quoting workflow for aéreo (air), LCL (Less than Container Load), and FCL (Full Container Load) freight. The system handles inbound quote requests by email or WhatsApp, pulls live rate cards from SharePoint/Drive, builds a proforma, routes it through a human approval gate, and sends the signed PDF from the responsible staff member's email address.

**Acceptance criterion ("no funciona, no me pagas"):** Three consecutive quotes approved and sent by JP (or designee) without rewriting any line. Demonstrates pattern, not luck.

---

## Key People

| Name | Role | Contact | Notes |
|---|---|---|---|
| Jean Paul (JP) | Co-founder / Partner | jparrue@gt.com.pe · (+51) 994 158 380 | Primary operational decision-maker and cotizador spec owner. He catches detail errors. |
| Renato | Co-founder / Partner | ralvarez@gt.com.pe · (+51) 998 348 636 | Barney's personal contact — relationship-driven. Collapses scope categories at decision time ("es lo mismo"). Always disambiguate scope in writing. |
| Vania | Procurement / Admin | comm.intel@gt.com.pe | Inbound contact; manages BASC/ISO registration. WhatsApp for quick, email for formal. |
| Abel | Commercial staff | pricing@gt.com.pe · (+51) 983 421 482 | Day-to-day quoter who designed the workflow. Staff code GT-PC. Source of truth for rate confirmations. |
| Daniela | Commercial staff | lognet.sales@gt.com.pe | Day-to-day quoter; staff code GT-LOC. |
| Cielo | Commercial staff | wca.sales@gt.com.pe | Day-to-day quoter; staff code GT-WCA. |
| Kristel | Operations supervisor | TBD | Relevant for future pipelines. |
| dduque@stconsac.com (STC SAC) | GT's IT provider | dduque@stconsac.com | Manages GT's Microsoft 365. All comms must CC Renato. |
| Jesús Diez — SINTAD | SINTAD technical lead | jdiez@sintad.pe | Custom Java API on SCE Carga. Sandbox on his server. |
| Katherine Alzamora — SINTAD | SINTAD commercial | kalzamora@sintad.pe | Primary SINTAD coordination contact. |

---

## Ground Rules

- **Payments always require human approval — never automate money movement.**
- Every financial action leaves an audit trail.
- Client data never mixes between projects.
- AI suggests. Human decides.
- **Approval gate is non-negotiable** (BASC requirement) — no email sends without explicit human approval.
- BASC-grade credential handling from day one: secrets manager, no plaintext, access logs, audit trail.

---

## Build Philosophy

### Scope is Pipeline #1 only
The cotizador covers quoting + approval + send. WCA outreach, 24/7 multilingual, commercial KPIs, and SINTAD write-back are future paid pipelines. Never absorb scope silently — flag it.

### Multilingual is English + Spanish only
German proformas, Mandarin PDFs, French T&Cs = Pipeline #3. Don't extend templates without a formal scope discussion.

### Tests are mandatory
522/522 passing as of 2026-06-11. No commit that drops test count. Every new route or parsing function gets unit tests.

### SINTAD is manual through Pipeline #1
SINTAD double-entry continues until Pipeline #1 is accepted. `sintad_export.py.new` is scaffolded but not wired.

### IGV applied ONCE — by the PDF layer
Routes.py stores pre-IGV net values. The PDF layer applies 18% at render. Never store IGV-inclusive values as venta_neto.

### Rate cards are the source of truth; hardcoded values need confirmation
VB rates, CIF calc minimums, flat local charges, and LCL transport bands must be confirmed with Abel/Vania before any production quote. See `AUDIT_RECONCILIATION_2026-06-12.md` for full status (MATCH / DRIFT / MISSING / UNVERIFIED per component).

### Live system
Deployed on Railway. GitHub auto-deploy on push to main. Never run `railway up` unless GitHub deploy is broken. Confirm health: `curl https://global-transport-cotizador-production.up.railway.app/health`.

---

## Stress Test Mandate

Before any demo or go-live:
- Run the approval flow end-to-end: quote created → detail shows margin widget → approved at correct margin → MARGIN_OVERRIDE event in audit log → status APPROVED → PDF sent.
- The approval flow stress test (8/8 pass) must pass clean.
- Three consecutive quotes covering LCL / Aéreo / FCL must each complete the full lifecycle without rewriting.

---

## Current Status (2026-06-13)

- 522/522 tests passing.
- LISTENER_ENABLED=false — listener off until JP/Renato give go-live approval.
- Abel confirmed "todo lo demás está conforme" after Demo 2 (PDF format was the last issue; now fixed).
- Pending: Abel config session (default line items, auto-add rules, transport cost logic), Renato/JP go-live session, 3 consecutive approved quotes to trigger USD 750 saldo.

---

## Rate Card Files (SharePoint / Graph API)

| File | Sheets | Access |
|---|---|---|
| TARIFARIO AGENTES EXPO GT - 2025 - V2.xlsx | FCL EXW/FOB EXPO, LCL EXW/FOB EXPO, AIR EXW/FCA EXPO | `get_rate_cards("expo")` |
| TARIFARIO AGENTES IMPO GT - 2025 - V2.xlsx | FCL DDP/DAP IMPO, LCL DDP/DAP IMPO, AIR DAP IMPO | `get_rate_cards("impo")` |
| HANDLING AEREO.xlsx | TALMA, SHOHIN, SAASA | `get_air_handling_fees()` |
| DATA COLOADERS.xlsx | Contact info only (not rates) | Seeded once via seed_reference_data.py |
| LISTA CRÉDITOS.xlsx | 61 approved counterparties | Seeded once via seed_reference_data.py |

---

## Key Architecture Files

```
cotizador/
├── core/
│   ├── transport.py          # VB rates, LCL bands, customs broker
│   ├── drive.py              # SharePoint Graph API + rate card parsing
│   ├── db.py                 # SQLite schema and queries
│   ├── email_sender.py       # Graph API Mail.Send per ejecutivo
│   ├── email_listener.py     # Inbound email polling + routing
│   ├── provider_reply_parser.py  # Provider reply detection + rate extraction
│   └── pdf_generator.py      # WeasyPrint PDF (IGV applied here)
├── api/
│   └── routes.py             # Flask routes + approval gate + auto-send
├── procedures/
│   └── rules.py              # GT ISO 9001 business rules (GT-P-001..017)
├── templates/
│   ├── new_quote.html        # Quote form (CBM calc, multi-package, CIF calc)
│   ├── quote_detail.html     # Approval gate + margin widget
│   ├── proforma_es.html      # PDF template — Spanish
│   └── proforma_en.html      # PDF template — English
└── tests/                    # 522 tests total
```

---

## Project Rule: Audit Before Build

Before writing any code for a new feature or integration:
1. Every source-of-truth file (rate cards, tariff sheets, Excel references) must be
   explicitly READ and its contents RECONCILED against what the code does — not assumed
   to have been seen because it exists in a shared folder.
2. When a client provides new files mid-engagement, they are treated as UNSEEN until
   audited. "It's in Drive" is not the same as "we've read it." Files get audited,
   reconciled, and added to the build spec before any code changes.
3. The reconciliation (MATCH / DRIFT / MISSING / UNVERIFIED per component) becomes the
   build spec. Code is written against that spec, not against assumptions.
4. If new files surface scope beyond what was committed, flag it explicitly before
   building — never absorb it silently.
