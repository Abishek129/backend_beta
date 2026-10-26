"""RTM band metadata + per-metric default thresholds.

Each metric is shown as a raw value in the grid (``display_col``) but **banded**
on a normalized value (``basis_col``) so one threshold set works across feeders
of any size (a 900 kW incomer and a 380 kW UPS feeder both band on % of rated).
``direction='asc'`` → higher value = worse (kW/Amps/kVAr/Voltage-deviation/
unbalance); ``'desc'`` → higher value = better (Power Factor). ``absval`` bands
on |value| (voltage deviation can be ±).

Resolution order for the 4 boundaries: per-Group RtmThreshold → per-panel_type
RtmThreshold → DEFAULT_BANDS here.
"""

# metric -> (display_col, basis_col, direction, absval)
METRIC_META = {
    'kw':      ('active_power_total_kw',     'kpi_kw_load_pct_of_rated',  'asc',  False),
    'kvar':    ('reactive_power_total_kvar', 'ratio_kvar_kva',            'asc',  False),
    'pf':      ('power_factor_total',        'power_factor_total',        'desc', False),
    'volt':    ('voltage_ll_avg',            'kpi_voltage_deviation_pct', 'asc',  True),
    'amp':     ('current_avg',               'kpi_kva_load_pct_of_rated', 'asc',  False),
    'i_unbal': ('current_unbalance_pct',     'current_unbalance_pct',     'asc',  False),
    'kva':     ('apparent_power_total_kva',  'kpi_kva_load_pct_of_rated', 'asc',  False),   # item 7
    'v_unbal': ('voltage_unbalance_pct',     'voltage_unbalance_pct',     'asc',  False),   # item 7
}

# kvar basis (ratio_kvar_kva) is 0..1 in the DB; scale to % so its bounds read like the others
BASIS_SCALE = {'ratio_kvar_kva': 100.0}

# metric -> (low_max, normal_max, moderate_max, high_max) in the basis units
DEFAULT_BANDS = {
    'kw':      (40.0, 60.0, 80.0, 95.0),     # % of rated kW
    'kvar':    (20.0, 40.0, 60.0, 80.0),     # kVAr/kVA %
    'pf':      (0.98, 0.95, 0.90, 0.85),     # raw PF (descending: >=0.98 -> Low)
    'volt':    (1.0, 2.0, 3.0, 5.0),         # |deviation %|
    'amp':     (40.0, 60.0, 80.0, 95.0),     # % of rated kVA
    'i_unbal': (5.0, 10.0, 15.0, 20.0),      # unbalance %
    'kva':     (40.0, 60.0, 80.0, 95.0),     # % of rated kVA (item 7)
    'v_unbal': (1.0, 2.0, 3.0, 5.0),         # voltage unbalance % (item 7)
}

BANDS = ['low', 'normal', 'moderate', 'high', 'critical']


def classify(value, bounds, direction):
    """value + 4 ascending boundary edges -> band name (5-way)."""
    if value is None:
        return 'normal'
    lo, no, mo, hi = bounds
    if direction == 'desc':                  # higher = better (PF)
        if value >= lo: return 'low'
        if value >= no: return 'normal'
        if value >= mo: return 'moderate'
        if value >= hi: return 'high'
        return 'critical'
    if value < lo: return 'low'              # higher = worse
    if value < no: return 'normal'
    if value < mo: return 'moderate'
    if value < hi: return 'high'
    return 'critical'
