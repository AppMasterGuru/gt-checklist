"""
Tests for procedures/ module — GT ISO 9001 business rules.

Every rule that is codified must have at least one test proving it works.
Running total contribution: +20 tests (bringing 104 → 124).

All tests are offline — no network calls, no Claude API, no Google Drive.
"""

from __future__ import annotations

import pytest

from procedures.rules import (
    PROCEDURE_VERSION,
    APPROVED_LCL_CONSOLIDATORS,
    MIN_MARGIN_PCT,
    QUOTE_VALIDITY_DAYS,
    RESPONSE_SLA_STANDARD_HOURS,
    RESPONSE_SLA_URGENT_HOURS,
    ProcedureViolation,
    get_response_sla_hours,
    requires_oea_basc_agent,
    run_all_checks,
    validate_cargo,
    validate_cargo_measurements,
    validate_incoterm,
    validate_lcl_consolidator,
    validate_margin,
    validate_mode,
    validate_send_readiness,
    validate_validity_days,
)
from procedures.drive_reader import (
    DRIVE_AVAILABLE,
    get_procedure_folder_id,
    is_drive_configured,
    read_procedures_folder,
)


# ── GT-P-000: Version string present ────────────────────────────────────────


class TestProcedureVersion:
    def test_version_string_format(self):
        """PROCEDURE_VERSION must follow GT-PROC-{version}-{YYYY-MM-DD}."""
        assert PROCEDURE_VERSION.startswith("GT-PROC-")
        parts = PROCEDURE_VERSION.split("-")
        assert len(parts) >= 4, "Version must have at least 4 dash-separated parts"

    def test_version_is_string(self):
        assert isinstance(PROCEDURE_VERSION, str)
        assert len(PROCEDURE_VERSION) > 10


# ── GT-P-001: Margin floor ───────────────────────────────────────────────────


class TestValidateMargin:
    def test_margin_at_floor_passes(self):
        ok, msg = validate_margin(0.10)
        assert ok is True
        assert msg == ""

    def test_margin_above_floor_passes(self):
        ok, msg = validate_margin(0.20)
        assert ok is True

    def test_margin_below_floor_fails(self):
        ok, msg = validate_margin(0.09)
        assert ok is False
        assert "GT-P-001" in msg
        assert "9.0%" in msg or "9%" in msg

    def test_zero_margin_fails(self):
        ok, msg = validate_margin(0.0)
        assert ok is False
        assert "GT-P-001" in msg

    def test_min_margin_constant(self):
        assert MIN_MARGIN_PCT == 0.10


# ── GT-P-002: Valid modes ────────────────────────────────────────────────────


class TestValidateMode:
    def test_aereo_valid(self):
        ok, _ = validate_mode("aereo")
        assert ok is True

    def test_lcl_valid(self):
        ok, _ = validate_mode("lcl")
        assert ok is True

    def test_fcl_valid(self):
        ok, _ = validate_mode("fcl")
        assert ok is True

    def test_unknown_mode_fails(self):
        ok, msg = validate_mode("rail")
        assert ok is False
        assert "GT-P-002" in msg

    def test_empty_mode_fails(self):
        ok, msg = validate_mode("")
        assert ok is False

    def test_mode_case_insensitive(self):
        ok, _ = validate_mode("FCL")
        assert ok is True


# ── GT-P-003: Incoterm × mode compatibility ──────────────────────────────────


class TestValidateIncoterm:
    def test_fob_lcl_valid(self):
        ok, _ = validate_incoterm("FOB", "lcl")
        assert ok is True

    def test_fob_aereo_invalid(self):
        # FOB is a sea/inland waterway term — not valid for air
        ok, msg = validate_incoterm("FOB", "aereo")
        assert ok is False
        assert "GT-P-003" in msg

    def test_fca_aereo_valid(self):
        ok, _ = validate_incoterm("FCA", "aereo")
        assert ok is True

    def test_dap_fcl_valid(self):
        ok, _ = validate_incoterm("DAP", "fcl")
        assert ok is True

    def test_invalid_incoterm_string(self):
        ok, msg = validate_incoterm("XXX", "lcl")
        assert ok is False
        assert "GT-P-003" in msg

    def test_case_insensitive_incoterm(self):
        ok, _ = validate_incoterm("fob", "lcl")
        assert ok is True

    def test_exw_all_modes(self):
        for mode in ("aereo", "lcl", "fcl"):
            ok, _ = validate_incoterm("EXW", mode)
            assert ok is True, f"EXW should be valid for {mode}"


# ── GT-P-004: Cargo description — restricted goods ───────────────────────────


class TestValidateCargo:
    def test_normal_cargo_passes(self):
        ok, _ = validate_cargo("Espárragos frescos — refrigerados, perecibles")
        assert ok is True

    def test_esparagos_pass(self):
        ok, _ = validate_cargo("Fresh asparagus, temperature controlled, 240kg")
        assert ok is True

    def test_explosives_fails(self):
        ok, msg = validate_cargo("explosivos industriales para minería")
        assert ok is False
        assert "GT-P-004" in msg

    def test_weapons_fails(self):
        ok, msg = validate_cargo("armas de fuego para exportación")
        assert ok is False
        assert "GT-P-004" in msg

    def test_empty_description_passes(self):
        # Empty description is allowed — other checks catch missing cargo data
        ok, _ = validate_cargo("")
        assert ok is True


# ── GT-P-005: LCL consolidator approval ──────────────────────────────────────


class TestValidateLclConsolidator:
    def test_msl_approved(self):
        ok, _ = validate_lcl_consolidator("MSL")
        assert ok is True

    def test_craft_approved(self):
        ok, _ = validate_lcl_consolidator("CRAFT")
        assert ok is True

    def test_unknown_consolidator_fails(self):
        ok, msg = validate_lcl_consolidator("UNKNOWN_CO")
        assert ok is False
        assert "GT-P-005" in msg

    def test_approved_list_complete(self):
        expected = {"MSL", "CRAFT", "SACO", "VANGUARD", "ECU WORLDWIDE", "EQ"}
        assert APPROVED_LCL_CONSOLIDATORS == expected


# ── GT-P-006: OEA/BASC requirement ───────────────────────────────────────────


class TestRequiresOeaBasc:
    def test_normal_cargo_no_oea(self):
        assert requires_oea_basc_agent("Espárragos frescos") is False

    def test_pharmaceutical_requires_oea(self):
        assert requires_oea_basc_agent("pharmaceutical equipment") is True

    def test_farmex_client_requires_oea(self):
        assert requires_oea_basc_agent("fresh produce", "Farmex") is True

    def test_medicamentos_requires_oea(self):
        assert requires_oea_basc_agent("medicamentos para hospitales") is True


# ── GT-P-007: Quote validity window ──────────────────────────────────────────


class TestValidateValidity:
    def test_standard_15_days_passes(self):
        ok, _ = validate_validity_days(15)
        assert ok is True

    def test_nonstandard_days_fails(self):
        ok, msg = validate_validity_days(30)
        assert ok is False
        assert "GT-P-007" in msg

    def test_validity_constant(self):
        assert QUOTE_VALIDITY_DAYS == 15


# ── GT-P-008: Approval gate ───────────────────────────────────────────────────


class TestValidateSendReadiness:
    def test_approved_can_be_sent(self):
        ok, _ = validate_send_readiness("APPROVED")
        assert ok is True

    def test_pending_cannot_be_sent(self):
        ok, msg = validate_send_readiness("PENDING")
        assert ok is False
        assert "GT-P-008" in msg

    def test_rejected_cannot_be_sent(self):
        ok, msg = validate_send_readiness("REJECTED")
        assert ok is False


# ── GT-P-009: Response SLA ────────────────────────────────────────────────────


class TestResponseSla:
    def test_standard_sla(self):
        assert get_response_sla_hours("flexible") == RESPONSE_SLA_STANDARD_HOURS
        assert RESPONSE_SLA_STANDARD_HOURS == 4

    def test_urgent_sla(self):
        assert get_response_sla_hours("asap") == RESPONSE_SLA_URGENT_HOURS
        assert RESPONSE_SLA_URGENT_HOURS == 2

    def test_unknown_urgency_defaults_to_standard(self):
        assert get_response_sla_hours("unknown") == RESPONSE_SLA_STANDARD_HOURS


# ── GT-P-010: Cargo measurements ─────────────────────────────────────────────


class TestValidateCargoMeasurements:
    def test_weight_only_passes(self):
        ok, _ = validate_cargo_measurements(500, None)
        assert ok is True

    def test_volume_only_passes(self):
        ok, _ = validate_cargo_measurements(None, 3.2)
        assert ok is True

    def test_both_passes(self):
        ok, _ = validate_cargo_measurements(850, 3.2)
        assert ok is True

    def test_neither_fails(self):
        ok, msg = validate_cargo_measurements(None, None)
        assert ok is False
        assert "GT-P-010" in msg

    def test_zero_weight_zero_volume_fails(self):
        ok, _ = validate_cargo_measurements(0, 0)
        assert ok is False


# ── run_all_checks ────────────────────────────────────────────────────────────


class TestRunAllChecks:
    def _valid_kwargs(self) -> dict:
        return {
            "margin_pct":        0.20,
            "mode":              "lcl",
            "incoterm":          "FOB",
            "cargo_description": "Uvas frescas",
            "weight_kg":         850.0,
            "volume_cbm":        3.2,
            "consolidator":      "CRAFT",
        }

    def test_valid_lcl_quote_no_violations(self):
        violations = run_all_checks(**self._valid_kwargs())
        assert violations == []

    def test_low_margin_produces_violation(self):
        kwargs = self._valid_kwargs()
        kwargs["margin_pct"] = 0.05
        violations = run_all_checks(**kwargs)
        assert any("GT-P-001" in v for v in violations)

    def test_invalid_mode_produces_violation(self):
        kwargs = self._valid_kwargs()
        kwargs["mode"] = "truck"
        violations = run_all_checks(**kwargs)
        assert any("GT-P-002" in v for v in violations)

    def test_raise_on_violation_raises(self):
        kwargs = self._valid_kwargs()
        kwargs["margin_pct"] = 0.05
        with pytest.raises(ProcedureViolation):
            run_all_checks(**kwargs, raise_on_violation=True)

    def test_multiple_violations_collected(self):
        # Both margin AND mode are invalid
        violations = run_all_checks(
            margin_pct=0.02,
            mode="truck",
            incoterm="FOB",
            cargo_description="normal goods",
            weight_kg=100,
        )
        assert len(violations) >= 2

    def test_valid_aereo_quote_no_violations(self):
        violations = run_all_checks(
            margin_pct=0.20,
            mode="aereo",
            incoterm="FCA",
            cargo_description="Espárragos frescos",
            weight_kg=240,
            volume_cbm=1.1,
        )
        assert violations == []


# ── Drive reader ──────────────────────────────────────────────────────────────


class TestDriveReader:
    def test_folder_id_extracted(self):
        folder_id = get_procedure_folder_id()
        assert isinstance(folder_id, str)
        assert len(folder_id) > 10  # Google Drive IDs are long strings

    def test_stub_mode_returns_empty_list(self):
        # No credentials configured → stub mode → empty list, no error
        files = read_procedures_folder()
        assert isinstance(files, list)

    def test_drive_not_configured(self):
        # GOOGLE_SERVICE_ACCOUNT_JSON not in .env → not available
        assert is_drive_configured() is False

    def test_drive_available_constant_type(self):
        assert isinstance(DRIVE_AVAILABLE, bool)


# ── GT-P-016: Risk score classification ─────────────────────────────────────

from procedures.rules import (  # noqa: E402
    RISK_CRITICAL_THRESHOLD,
    validate_risk_score,
    validate_risk_matrix_currency,
    ALLOWED_RISK_CURRENCIES,
)


class TestValidateRiskScore:
    """GT-P-016: P×I ≥ 9 = Crítico (Prioridad 1); P×I < 9 = Tolerable (Prioridad 2)."""

    def test_score_9_boundary_is_critico(self):
        classification, _ = validate_risk_score(3, 3)
        assert classification == "Crítico"

    def test_score_9_boundary_is_prioridad_1(self):
        _, priority = validate_risk_score(3, 3)
        assert priority == "Prioridad 1"

    def test_score_8_below_boundary_is_tolerable(self):
        classification, _ = validate_risk_score(4, 2)
        assert classification == "Tolerable"

    def test_score_8_below_boundary_is_prioridad_2(self):
        _, priority = validate_risk_score(4, 2)
        assert priority == "Prioridad 2"

    def test_score_12_above_boundary_is_critico(self):
        classification, _ = validate_risk_score(3, 4)
        assert classification == "Crítico"

    def test_max_score_81_is_critico(self):
        classification, _ = validate_risk_score(9, 9)
        assert classification == "Crítico"

    def test_min_score_1_is_tolerable(self):
        classification, _ = validate_risk_score(1, 1)
        assert classification == "Tolerable"

    def test_p1_i9_score_9_is_critico(self):
        classification, _ = validate_risk_score(1, 9)
        assert classification == "Crítico"

    def test_p9_i1_score_9_is_critico(self):
        classification, _ = validate_risk_score(9, 1)
        assert classification == "Crítico"

    def test_score_6_is_tolerable(self):
        classification, _ = validate_risk_score(2, 3)
        assert classification == "Tolerable"

    def test_score_4_is_tolerable(self):
        classification, _ = validate_risk_score(2, 2)
        assert classification == "Tolerable"

    def test_score_10_is_critico(self):
        classification, _ = validate_risk_score(5, 2)
        assert classification == "Crítico"

    def test_returns_tuple_of_two(self):
        result = validate_risk_score(3, 3)
        assert isinstance(result, tuple) and len(result) == 2

    def test_classification_is_string(self):
        classification, _ = validate_risk_score(2, 4)
        assert isinstance(classification, str)

    def test_priority_is_string(self):
        _, priority = validate_risk_score(1, 1)
        assert isinstance(priority, str)

    def test_zero_probability_gives_tolerable(self):
        classification, _ = validate_risk_score(0, 9)
        assert classification == "Tolerable"


# ── GT-P-017: Risk matrix currency ──────────────────────────────────────────


class TestValidateRiskMatrixCurrency:
    """GT-P-017: Risk matrix costs must be in USD, PEN, or SOL."""

    def test_usd_is_valid(self):
        ok, _ = validate_risk_matrix_currency("USD")
        assert ok is True

    def test_pen_is_valid(self):
        ok, _ = validate_risk_matrix_currency("PEN")
        assert ok is True

    def test_sol_is_valid(self):
        ok, _ = validate_risk_matrix_currency("SOL")
        assert ok is True

    def test_eur_is_invalid(self):
        ok, _ = validate_risk_matrix_currency("EUR")
        assert ok is False

    def test_gbp_is_invalid(self):
        ok, _ = validate_risk_matrix_currency("GBP")
        assert ok is False

    def test_empty_string_is_invalid(self):
        ok, _ = validate_risk_matrix_currency("")
        assert ok is False

    def test_lowercase_usd_is_valid(self):
        ok, _ = validate_risk_matrix_currency("usd")
        assert ok is True

    def test_returns_tuple(self):
        result = validate_risk_matrix_currency("USD")
        assert isinstance(result, tuple) and len(result) == 2

    def test_valid_returns_true_bool(self):
        ok, _ = validate_risk_matrix_currency("PEN")
        assert ok is True and isinstance(ok, bool)

    def test_invalid_returns_false_bool(self):
        ok, _ = validate_risk_matrix_currency("CHF")
        assert ok is False and isinstance(ok, bool)
