"""Per-type config defaults.

Resolution order for any config field: the per-row PanelConfig / MfmConfig
override -> the type default here -> None. These are sensible placeholders for
the Seetharampur plant; refine per row via the admin.

Frontend nameplate keys are mapped from these in core.config (_frontend_config).
"""

# 2500 kVA 11kV/415V LV distribution transformers (8×), 0.85 pf.
# rated_current_a at 415 V LL: 2500_000 / (sqrt(3) * 415) ≈ 3478 A.
TRANSFORMER = {
    'rated_kva': 2500.0,
    'rated_kw': 2125.0,
    'contracted_kw': 2000.0,
    'critical_load_kw': 1500.0,
    'nominal_voltage_v': 415.0,
    'voltage_statutory_deviation_pct': 10.0,
    'rated_current_a': 3478.0,
    'current_tolerance_pct': 20.0,
    'current_high_threshold_a': 3478.0,
    'energy_target_kwh_today': 40000.0,
    'subsidy_limit_kw': 2000.0,
    'subsidy_limit_mvah': 1600.0,
    'target_efficiency_pct': 99.0,
    'thd_v_limit_pct': 5.0,
    'thd_i_limit_pct': 8.0,
    'true_pf_min': 0.9,
    'neutral_current_limit_a': 30.0,
    'frequency_hz': 50.0,
}

# PCC / HT distribution buses — sized to the transformer pair feeding them.
DISTRIBUTION_PANEL = {
    'rated_kva': 5000.0,
    'rated_kw': 4250.0,
    'contracted_kw': 4000.0,
    'critical_load_kw': 2700.0,
    'nominal_voltage_v': 415.0,
    'voltage_statutory_deviation_pct': 10.0,
    'rated_current_a': 6956.0,
    'current_tolerance_pct': 20.0,
    'current_high_threshold_a': 6956.0,
    'energy_target_kwh_today': 80000.0,
    'subsidy_limit_kw': 4000.0,
    'subsidy_limit_mvah': 3200.0,
    'target_efficiency_pct': 99.0,
    'thd_v_limit_pct': 5.0,
    'thd_i_limit_pct': 8.0,
    'true_pf_min': 0.9,
    'neutral_current_limit_a': 30.0,
    'frequency_hz': 50.0,
}

# LT feeders / load panels — typical 500 kW outgoing feeder.
LT_PANEL = {
    'rated_kva': 588.0,
    'rated_kw': 500.0,
    'contracted_kw': 450.0,
    'critical_load_kw': 270.0,
    'nominal_voltage_v': 415.0,
    'voltage_statutory_deviation_pct': 10.0,
    'rated_current_a': 818.0,
    'current_tolerance_pct': 20.0,
    'current_high_threshold_a': 818.0,
    'energy_target_kwh_today': 1200.0,
    'subsidy_limit_kw': 450.0,
    'subsidy_limit_mvah': 360.0,
    'target_efficiency_pct': 97.0,
    'thd_v_limit_pct': 5.0,
    'thd_i_limit_pct': 8.0,
    'true_pf_min': 0.9,
    'neutral_current_limit_a': 30.0,
    'frequency_hz': 50.0,
}

BY_PANEL_TYPE = {
    'transformer': TRANSFORMER,
    'distribution_panel': DISTRIBUTION_PANEL,
    'lt_panel': LT_PANEL,
}


def defaults_for(panel_type_code):
    return BY_PANEL_TYPE.get(panel_type_code, LT_PANEL)
