"""Shared voltage-basis and numeric-event rules for EMS pages.

P4's existing real-logger profile stores voltage_avg in L-L volts. Legacy
sources retain their L-N convention. Never change or overwrite logger values.
"""
import datetime as dt
import time

from core.services import is_real, fetch_threshold_events


class WindowMetrics:
    """Connection-local 24h crossing counts; at most one query per minute."""
    def __init__(self):
        self.expires = 0
        self.values = {}

    def get(self, ctx, anchor):
        now = time.monotonic()
        if now >= self.expires:
            specs = {**voltage_events(ctx.config, ctx.dsn),
                     'current_events': ('current_unbalance_pct', 'above', 10),
                     'neutral_events': ('current_neutral', 'above', 30)}
            rows = fetch_threshold_events(ctx.dsn, ctx.table, anchor - dt.timedelta(hours=24),
                                          anchor, None, specs, ctx.time_col)
            self.values = {key: (rows[0].get(key, 0) if rows else 0) for key in specs}
            self.expires = now + 60
        return dict(self.values)


def voltage_nominal(config, dsn):
    if is_real(dsn):
        return config.get('nominal_voltage_ll_v')
    return config.get('nominal_voltage_ln_v') or config.get('nominal_voltage_v')


def voltage_events(config, dsn):
    nominal = voltage_nominal(config, dsn)
    ll = config.get('nominal_voltage_ll_v') or 0
    if ll > 1000 and nominal:
        return {'sag_events': ('voltage_avg', 'below', .9 * nominal),
                'swell_events': ('voltage_avg', 'above', 1.1 * nominal)}
    return {'sag_events': ('kpi_voltage_deviation_pct', 'below', -10),
            'swell_events': ('kpi_voltage_deviation_pct', 'above', 10)}


def voltage_window(row, config, dsn):
    nominal = voltage_nominal(config, dsn)
    peak = row.get('voltage_peak')
    floor = .5 * nominal if nominal else 100
    online = peak is not None and peak >= floor
    row['online'] = online
    if not online:
        row['voltage_deviation_pct'] = None
    elif (config.get('nominal_voltage_ll_v') or 0) > 1000 and nominal:
        values = [row.get('voltage_peak'), row.get('voltage_low')]
        row['voltage_deviation_pct'] = max(
            (abs((v - nominal) / nominal * 100) for v in values if v is not None),
            default=None)
    else:
        values = [row.get('voltage_deviation_pct'), row.get('voltage_deviation_min_pct')]
        row['voltage_deviation_pct'] = max((abs(v) for v in values if v is not None), default=None)
    return row
