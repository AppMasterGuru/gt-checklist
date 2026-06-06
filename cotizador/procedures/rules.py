"""
GT ISO 9001 Business Rules — Cotizador Procedure Module.

Version: GT-PROC-1.0-2026-05-16
Source: GT SIG ISO 9001 procedures + Abel Díaz Peralta demo (2026-05-07)
        + Jean Paul Arrue business review (2026-04-29)

BASC audit requirement: every quote creation must log PROCEDURE_VERSION_USED
to the audit_log table. The version string is PROCEDURE_VERSION below.

Each validator returns (ok: bool, message: str).
  ok=True  → rule satisfied, message is empty string
  ok=False → rule violated, message explains why

Rules are numbered (GT-P-001 … GT-P-010) for BASC traceability.
When a new version of the SIG procedures changes a rule, bump PROCEDURE_VERSION
and update the rule number. Auditors can then trace which version governed
any given quote.
"""

from __future__ import annotations

# ── Version tracking (BASC requirement) ──────────────────────────────────────

PROCEDURE_VERSION = "GT-PROC-1.0-2026-05-16"
"""
Immutable version string. Include in every quote's audit trail entry.
Format: GT-PROC-{major}.{minor}-{YYYY-MM-DD of this version}
"""

# ── GT-P-001: Margin floor ────────────────────────────────────────────────────
# Source: Abel Díaz Peralta (2026-05-07 demo): "10% is the minimum — below that
#         it's not a business." Confirmed by Jean Paul.

MIN_MARGIN_PCT: float = 0.10   # 10%


def validate_margin(margin_pct: float) -> tuple[bool, str]:
    """
    GT-P-001: Every quote must carry at least MIN_MARGIN_PCT (10%) gross margin.

    Args:
        margin_pct: Fraction (0.0–1.0), not percentage.

    Returns:
        (True, "") if ok, (False, reason) if violated.
    """
    if margin_pct >= MIN_MARGIN_PCT:
        return True, ""
    pct_formatted = f"{margin_pct * 100:.1f}%"
    floor_formatted = f"{MIN_MARGIN_PCT * 100:.0f}%"
    return (
        False,
        f"GT-P-001: Margin {pct_formatted} is below the required minimum of {floor_formatted}. "
        "Approval gate will block this quote.",
    )


# ── GT-P-002: Valid shipping modes ───────────────────────────────────────────
# Source: Abel demo — GT handles exactly three modes.

VALID_MODES: frozenset[str] = frozenset({"aereo", "lcl", "fcl"})


def validate_mode(mode: str) -> tuple[bool, str]:
    """
    GT-P-002: Mode must be one of: aereo, lcl, fcl.
    """
    if (mode or "").lower() in VALID_MODES:
        return True, ""
    return (
        False,
        f"GT-P-002: Mode {mode!r} is not valid. Accepted: aereo / lcl / fcl.",
    )


# ── GT-P-003: Incoterm × mode compatibility ──────────────────────────────────
# Source: Abel demo + Jean Paul business review.
# Not every incoterm makes commercial sense for every mode.

_INCOTERMS_BY_MODE: dict[str, frozenset[str]] = {
    "aereo": frozenset({"EXW", "FCA", "CPT", "CIP", "DAP", "DDP"}),
    "lcl":   frozenset({"EXW", "FCA", "FOB", "CFR", "CIF", "DAP", "DDP"}),
    "fcl":   frozenset({"EXW", "FCA", "FOB", "CFR", "CIF", "CPT", "CIP",
                        "DAP", "DPU", "DDP"}),
}

ALL_VALID_INCOTERMS: frozenset[str] = frozenset(
    {"EXW", "FCA", "FOB", "CFR", "CIF", "CPT", "CIP", "DAP", "DPU", "DDP"}
)


def validate_incoterm(incoterm: str, mode: str) -> tuple[bool, str]:
    """
    GT-P-003: Incoterm must be (a) a recognised ICC incoterm, and
              (b) compatible with the selected shipping mode.
    """
    inco = (incoterm or "").upper().strip()
    m = (mode or "").lower().strip()

    if inco not in ALL_VALID_INCOTERMS:
        return (
            False,
            f"GT-P-003: {inco!r} is not a recognised incoterm. "
            f"Valid: {sorted(ALL_VALID_INCOTERMS)}",
        )

    allowed = _INCOTERMS_BY_MODE.get(m, frozenset())
    if inco not in allowed:
        return (
            False,
            f"GT-P-003: Incoterm {inco!r} is not compatible with mode {m!r}. "
            f"Allowed for {m!r}: {sorted(allowed)}",
        )

    return True, ""


# ── GT-P-004: Cargo description — restricted goods check ─────────────────────
# Source: GT BASC standard 6.0 — restricted/prohibited cargo.
# Keywords trigger a manual review flag, not automatic rejection.

_RESTRICTED_CARGO: frozenset[str] = frozenset({
    "explosivo", "explosivos", "explosive", "explosives",
    "radioactivo", "radioactive",
    "arma", "armas", "weapon", "weapons",
    "municion", "municiones", "munition", "ammunition",
    "narcotico", "narcótico", "narcotic",
    "peligroso clase 1", "hazmat class 1",
    "hazardous class 1",
})


def validate_cargo(cargo_description: str) -> tuple[bool, str]:
    """
    GT-P-004: Cargo description must not contain restricted goods keywords.
    Returns (False, reason) when restricted keywords are detected — these
    require manual BASC compliance review before quoting.
    """
    lower = (cargo_description or "").lower()
    hits = [kw for kw in _RESTRICTED_CARGO if kw in lower]
    if hits:
        return (
            False,
            f"GT-P-004: Cargo description contains restricted goods keywords: {hits}. "
            "Manual BASC compliance review required before quoting.",
        )
    return True, ""


# ── GT-P-005: LCL consolidator approval ──────────────────────────────────────
# Source: Abel demo — GT only uses approved consolidators for LCL.

APPROVED_LCL_CONSOLIDATORS: frozenset[str] = frozenset({
    "MSL", "CRAFT", "SACO", "VANGUARD", "ECU WORLDWIDE",
})


def validate_lcl_consolidator(consolidator: str) -> tuple[bool, str]:
    """
    GT-P-005: LCL quotes must use a pre-approved consolidator.
    """
    c = (consolidator or "").upper().strip()
    if c in APPROVED_LCL_CONSOLIDATORS:
        return True, ""
    return (
        False,
        f"GT-P-005: Consolidator {c!r} is not on the approved list. "
        f"Approved: {sorted(APPROVED_LCL_CONSOLIDATORS)}",
    )


# ── GT-P-006: OEA/BASC customs agent requirement ─────────────────────────────
# Source: Abel demo — certain sectors require OEA+BASC certified customs agent.
# Default agent: Alefero. OEA+BASC triggers when sector keywords match.

_OEA_BASC_SECTORS: frozenset[str] = frozenset({
    "farmaceutico", "farmacéutico", "pharmaceutical", "pharma",
    "medicina", "medicamentos", "drugs", "medical",
    "alimento controlado", "controlled food",
    "quimico peligroso", "chemical hazardous",
    "farmex",  # client name known to require OEA+BASC
})


def requires_oea_basc_agent(cargo_description: str, client_name: str = "") -> bool:
    """
    GT-P-006: Return True when the cargo or client requires an OEA+BASC
    certified customs agent instead of the default (Alefero).
    """
    combined = f"{cargo_description} {client_name}".lower()
    return any(kw in combined for kw in _OEA_BASC_SECTORS)


# ── GT-P-007: Quote validity window ──────────────────────────────────────────
# Source: Jean Paul business review — all quotes expire after 15 days.

QUOTE_VALIDITY_DAYS: int = 15


def validate_validity_days(validity_days: int) -> tuple[bool, str]:
    """
    GT-P-007: Quote validity must be 15 days (GT standard).
    Deviations require partner approval.
    """
    if validity_days == QUOTE_VALIDITY_DAYS:
        return True, ""
    return (
        False,
        f"GT-P-007: Validity {validity_days} days deviates from GT standard "
        f"({QUOTE_VALIDITY_DAYS} days). Partner approval required.",
    )


# ── GT-P-008: Approval gate — quote must be APPROVED before sending ───────────
# Source: Jean Paul + BASC standard — no quote goes to client without approval.
# This rule is enforced at DB level (state machine trigger in schema.sql).
# Included here for completeness and testability.

APPROVAL_REQUIRED_STATUSES: frozenset[str] = frozenset({"PENDING", "APPROVED"})
SENDABLE_STATUSES: frozenset[str] = frozenset({"APPROVED"})


def validate_send_readiness(status: str) -> tuple[bool, str]:
    """
    GT-P-008: A quote may only be sent to the client when in APPROVED status.
    """
    if status == "APPROVED":
        return True, ""
    return (
        False,
        f"GT-P-008: Quote in status {status!r} cannot be sent. "
        "Status must be APPROVED before client delivery.",
    )


# ── GT-P-009: Response SLA ────────────────────────────────────────────────────
# Source: GT commercial standard — 4h for standard, 2h for urgent.

RESPONSE_SLA_STANDARD_HOURS: int = 4
RESPONSE_SLA_URGENT_HOURS: int = 2


def get_response_sla_hours(urgency: str = "flexible") -> int:
    """
    GT-P-009: Return the SLA response time in hours based on urgency.
    """
    return RESPONSE_SLA_URGENT_HOURS if urgency == "asap" else RESPONSE_SLA_STANDARD_HOURS


# ── GT-P-010: Weight + volume both required for transport calculation ──────────
# Source: Abel demo — transport calc requires BOTH weight and volume to apply
# the max(weight_rate, cbm_rate) rule correctly.

def validate_cargo_measurements(weight_kg: float | None, volume_cbm: float | None) -> tuple[bool, str]:
    """
    GT-P-010: At least one of weight_kg or volume_cbm must be provided.
    Both are strongly preferred for accurate transport calculation.
    """
    if weight_kg and weight_kg > 0:
        return True, ""
    if volume_cbm and volume_cbm > 0:
        return True, ""
    return (
        False,
        "GT-P-010: Neither weight_kg nor volume_cbm provided. "
        "At least one is required for transport cost calculation.",
    )


# ── GT-P-016: Risk score classification (GT-SIG-PR-002 §5) ──────────────────
# Source: GT-SIG-PR-002 §5 — P×I ≥ 9 = Crítico (Prioridad 1),
#         P×I < 9 = Tolerable (Prioridad 2).

RISK_CRITICAL_THRESHOLD: int = 9


def validate_risk_score(probability: int, impact: int) -> tuple[str, str]:
    """
    GT-P-016: Classify a risk by its probability × impact score.

    Returns:
        (classification, priority_level)
        e.g. ("Crítico", "Prioridad 1") or ("Tolerable", "Prioridad 2")
    """
    score = probability * impact
    if score >= RISK_CRITICAL_THRESHOLD:
        return "Crítico", "Prioridad 1"
    return "Tolerable", "Prioridad 2"


# ── GT-P-017: Risk matrix cost currency ──────────────────────────────────────
# Source: GT-SIG-PR-002 §6 — cost figures in the risk matrix must use USD or
#         PEN (Soles). Foreign currencies require conversion before entry.

ALLOWED_RISK_CURRENCIES: frozenset[str] = frozenset({"USD", "PEN", "SOL"})


def validate_risk_matrix_currency(currency: str) -> tuple[bool, str]:
    """
    GT-P-017: Risk matrix cost figures must be denominated in USD or PEN/SOL.

    Returns:
        (True, "") if valid, (False, reason) if not.
    """
    normalised = (currency or "").strip().upper()
    if normalised in ALLOWED_RISK_CURRENCIES:
        return True, ""
    return (
        False,
        f"GT-P-017: Currency {currency!r} is not accepted in risk matrices. "
        f"Use one of: {', '.join(sorted(ALLOWED_RISK_CURRENCIES))}.",
    )


# ── Aggregate validator ───────────────────────────────────────────────────────

class ProcedureViolation(Exception):
    """Raised when a quote violates one or more ISO 9001 procedure rules."""

    def __init__(self, violations: list[str]) -> None:
        self.violations = violations
        super().__init__("; ".join(violations))


def run_all_checks(
    *,
    margin_pct: float,
    mode: str,
    incoterm: str,
    cargo_description: str,
    client_name: str = "",
    weight_kg: float | None = None,
    volume_cbm: float | None = None,
    consolidator: str | None = None,
    validity_days: int = QUOTE_VALIDITY_DAYS,
    raise_on_violation: bool = False,
) -> list[str]:
    """
    Run all applicable procedure checks for a quote.

    Returns a list of violation messages (empty list = all checks pass).
    If raise_on_violation=True, raises ProcedureViolation on any failure.

    The returned list is suitable for logging to the audit trail.
    """
    violations: list[str] = []

    checks = [
        validate_margin(margin_pct),
        validate_mode(mode),
        validate_incoterm(incoterm, mode),
        validate_cargo(cargo_description),
        validate_cargo_measurements(weight_kg, volume_cbm),
        validate_validity_days(validity_days),
    ]

    # LCL-specific: consolidator check
    if mode == "lcl" and consolidator:
        checks.append(validate_lcl_consolidator(consolidator))

    for ok, msg in checks:
        if not ok:
            violations.append(msg)

    if violations and raise_on_violation:
        raise ProcedureViolation(violations)

    return violations
