"""Pure derived-parameter formulas (no DB).

Backend-computed replacements for params that used to be read straight from
simulator-fabricated columns (the "B -> A" move). These take already-fetched
scalars and return the derived value, so the meter only has to supply the
measured register set.
"""
import datetime as dt
import math


def _num(v):
    return float(v) if isinstance(v, (int, float)) and math.isfinite(v) else None


def kw_load_pct(active_kw, rated_kw):
    """kpi_kw_load_pct_of_rated = active power / rated kW × 100."""
    p, r = _num(active_kw), _num(rated_kw)
    if p is None or not r:
        return None
    return round(p / r * 100, 1)


def rate_per_min(prev, kw, ts):
    """rate_of_change_power_kw_per_min from consecutive samples.

    ``prev`` is the previous ``(kw, ts_datetime)`` or None; returns the new
    ``(value_or_None, (kw, ts))`` so the caller can carry ``prev`` forward.
    """
    kw = _num(kw)
    when = None
    if isinstance(ts, str):
        try:
            when = dt.datetime.fromisoformat(ts)
        except ValueError:
            when = None
    elif isinstance(ts, dt.datetime):
        when = ts
    val = None
    if prev and kw is not None and when is not None:
        dmin = (when - prev[1]).total_seconds() / 60.0
        if dmin > 0:
            val = round((kw - prev[0]) / dmin, 2)
    new_prev = (kw, when) if (kw is not None and when is not None) else prev
    return val, new_prev


def apparent_period(active_kwh, reactive_kvarh):
    """apparent_energy_*_kvah ≈ √(active² + reactive²) — there is no apparent
    import counter, so this is the standard approximation from the two counters.

    If the REACTIVE register is ABSENT (None, not a real 0), apparent is not
    computable — √(active²+None→0) collapses to exactly `active`, fabricating a
    PF=1 "apparent == active" (seen on HVAC meters whose reactive is never logged,
    e.g. AHU-01). Return None there instead. A genuine reactive 0 (real zero) still
    yields apparent = active, which is correct (unity PF)."""
    a, r = _num(active_kwh), _num(reactive_kvarh)
    if r is None:                      # no reactive → cannot synthesize a real apparent
        return None
    return round(math.hypot(a or 0.0, r), 2)


def voltage_deviation_pct(v, nominal):
    """(V − nominal)/nominal × 100 against the per-phase (L-N) nominal. Backend
    replacement for the logger's ``kpi_voltage_deviation_pct`` column, which the
    gic loggers baked against a 240 V base — garbage for 11 kV HT feeders."""
    v, n = _num(v), _num(nominal)
    if v is None or not n:
        return None
    return round((v - n) / n * 100, 2)


def trend_status(now, baseline, band=0.05):
    """Classify a metric vs its trailing baseline → 'rising' | 'stable' | 'falling'.
    ``band`` is the deadband fraction (default ±5%). Returns 'stable' when either
    value is missing (no spurious arrows)."""
    n, b = _num(now), _num(baseline)
    if n is None or b is None or b == 0:
        return 'stable'
    delta = (n - b) / abs(b)
    if delta > band:
        return 'rising'
    if delta < -band:
        return 'falling'
    return 'stable'
