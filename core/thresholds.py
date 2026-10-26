"""Page-specific presentation policies, not protection/trip settings.

Null configuration inherits a default; an explicit zero remains an override.
Keep event boundaries separate from badge boundaries (thresholds.md).
"""
import math


OVERVIEW_DEFAULTS = {
    'overview_busbar_temp_warn_c': 45., 'overview_busbar_temp_critical_c': 55.,
    'overview_load_elevated_pct': 75., 'overview_load_critical_pct': 90.,
    'overview_energy_elevated_pct': 75., 'overview_energy_critical_pct': 90.,
    'overview_pf_good': .95, 'overview_pf_fair': .90,
    'overview_freq_nominal_hz': 50., 'overview_freq_fair_dev_hz': .05,
    'overview_freq_unstable_dev_hz': .10,
    'overview_phase_watch_pct': 5., 'overview_phase_unbalanced_pct': 10.,
    'overview_voltage_dev_elevated_pct': 3., 'overview_voltage_dev_critical_pct': 5.,
}
PANEL_DEFAULTS = {
    'vc_vdev_warn_pct': 3., 'vc_vdev_danger_pct': 5.,
    'vc_iunbal_warn_pct': 5., 'vc_iunbal_danger_pct': 8.,
}


def setting(config, key, default):
    value = config.get(key)
    return default if value is None else value


def band(value, low, high, labels=('Normal', 'Elevated', 'Critical')):
    if value is None or not math.isfinite(float(value)):
        return None
    return labels[0] if value <= low else labels[1] if value <= high else labels[2]


def panel_vc_highlight(vdev, unbalance, config):
    v, i = abs(vdev or 0), unbalance or 0
    if (v > setting(config, 'vc_vdev_danger_pct', 5) or
            i > setting(config, 'vc_iunbal_danger_pct', 8)):
        return 'danger'
    if (v > setting(config, 'vc_vdev_warn_pct', 3) or
            i > setting(config, 'vc_iunbal_warn_pct', 5)):
        return 'warn'
    return 'success'


def panel_pq_severity(ithd, pf, config):
    limit = setting(config, 'thd_i_limit_pct', 8)
    if ((ithd is not None and ithd > limit) or
            (pf is not None and pf < setting(config, 'true_pf_min', .9))):
        return 'danger'
    return 'warning' if ithd is not None and ithd >= .8 * limit else 'success'


def feeder_energy_status(util, kw, pf, ithd):
    pf = abs(pf) if pf is not None else None
    pf_enabled = kw is not None and kw >= 1 and pf is not None and 0 < pf <= 1
    thd_enabled = kw is not None and kw >= 10 and ithd is not None
    if ((util is not None and util >= 100) or (pf_enabled and pf < .8) or
            (thd_enabled and ithd >= 15)):
        return 'danger'
    if ((util is not None and util >= 85) or (pf_enabled and pf < .9) or
            (thd_enabled and ithd >= 8)):
        return 'warning'
    return 'success'


def demand_band(value, limit):
    if value is None or not limit:
        return 'low'
    return 'high' if value > limit else 'moderate' if value >= .9 * limit else 'low'


def pq_state(values, limits, logged=None):
    pairs = [(v, limit) for v, limit in zip(values, limits) if v is not None]
    failed = any(v > limit for v, limit in pairs)
    verdict = str(logged).strip().upper()
    if verdict in {'PASS', 'FAIL'}:
        failed = verdict == 'FAIL'
    severity = 'High' if failed else 'Watch' if any(v >= .8 * lim for v, lim in pairs) else 'Low'
    return ('Fail' if failed else 'Pass'), severity


def flicker_tone(value, limit):
    return 'ok' if value is None or value < .7 * limit else 'watch' if value < limit else 'critical'


def crest_tone(value, ideal):
    d = abs(value - ideal) if value is not None else 0
    return 'ok' if d < .05 else 'watch' if d < .10 else 'critical'


def trend_status(current, baseline):
    if current is None or not baseline:
        return 'stable'
    change = (current - baseline) / abs(baseline)
    return 'rising' if change > .05 else 'falling' if change < -.05 else 'stable'
