"""Aggregate consumers for real distribution panels.

There is deliberately no panel timeseries binding: every member is queried via
its own MFM binding and then reduced by Membership.role.
"""
from __future__ import annotations

import datetime as dt
from concurrent.futures import ThreadPoolExecutor

from core.rtm_defaults import classify
from core.thresholds import (panel_vc_highlight, panel_pq_severity,
                             feeder_energy_status)
from core.services import (bucket_label, bucket_window, fetch_bucketed,
                           fetch_event_counts, fetch_live, fetch_sampled,
                           fetch_threshold_events, fetch_window, in_shift)
from core.time_filters import ALL_RANGES, TimeFilterPolicy
from panels.consumers.base import average, number, parse_timestamp
from panels.consumers.panel_base import FilteredPanelConsumer, LivePanelConsumer
from panels.consumers.derived import voltage_nominal, voltage_events, voltage_window
from panels.registry import SCREENS, screen

ENERGY_METRICS = {
    'active_kwh': ('delta', 'active_energy_import_kwh'),
    'reactive_kvarh': ('delta', 'reactive_energy_import_kvarh'),
    'apparent_kvah': ('delta', 'apparent_energy_kvah'),
}
POWER_METRICS = {
    'active_kw_avg': ('avg', 'active_power_total_kw'),
    'active_kw_max': ('max', 'active_power_total_kw'),
    'reactive_kvar_avg': ('avg', 'reactive_power_total_kvar'),
    'apparent_kva_avg': ('avg', 'apparent_power_total_kva'),
}
LIVE_COLUMNS = (
    'active_power_total_kw', 'reactive_power_total_kvar', 'apparent_power_total_kva',
    'power_factor_total', 'voltage_avg', 'voltage_r_n', 'voltage_y_n', 'voltage_b_n',
    'current_avg', 'current_r', 'current_y', 'current_b', 'current_neutral',
    'current_unbalance_pct', 'voltage_unbalance_pct', 'kpi_voltage_deviation_pct',
    'thd_compliance_i_avg', 'thd_compliance_v_avg', 'kpi_true_pf',
    'kpi_displacement_pf', 'harmonic_5_pct', 'harmonic_7_pct', 'k_factor',
    'thd_current_r_pct', 'thd_current_y_pct', 'thd_current_b_pct',
)
IST = dt.timezone(dt.timedelta(hours=5, minutes=30))


def _round(value, digits=2):
    return round(number(value), digits) if value is not None else None


def _pct(value, basis):
    return number(value) / number(basis) * 100 if number(basis) else None


def _parallel(members, func):
    if not members:
        return []
    # The UI opens Energy & Power four times concurrently. Keep each aggregate
    # below a quarter of the shared 16-connection LoggerFast pool, leaving room
    # for RTM and single-meter sockets rather than raising PoolError under load.
    with ThreadPoolExecutor(max_workers=min(2, len(members))) as pool:
        return list(pool.map(func, members))


def _sum(rows, key):
    values = [number(row.get(key)) for row in rows if row.get(key) is not None]
    return sum(values) if values else None


def _role_members(ctx, role):
    return [member for member in ctx.members if member.role == role]


def _member_identity(member):
    return {'mfm_id': member.eid, 'name': member.name, 'key': member.slug,
            'role': member.role, 'position': member.position}


def _member_live(member):
    raw = fetch_live(member.dsn, member.table, LIVE_COLUMNS, member.time_col) or {}
    row = {key: (value if key == 'ts' else number(value, None))
           for key, value in raw.items()}
    row.update(_member_identity(member))
    row['voltage_v'] = (row.get('voltage_avg') if row.get('voltage_avg') is not None else average(
        row.get('voltage_r_n'), row.get('voltage_y_n'), row.get('voltage_b_n')))
    row['current_a'] = (row.get('current_avg') if row.get('current_avg') is not None else average(
        row.get('current_r'), row.get('current_y'), row.get('current_b')))
    rated_kw = member.config.get('rated_kw')
    row['load_pct'] = _pct(row.get('active_power_total_kw'), rated_kw)
    return row


def _heatmap_row(member, bands):
    row = _member_live(member)
    kva_pct = _pct(row.get('apparent_power_total_kva'), member.config.get('rated_kva'))
    amp_pct = kva_pct
    nominal = voltage_nominal(member.config, member.dsn)
    vdev = row.get('kpi_voltage_deviation_pct')
    if (vdev is None or (nominal and nominal > 1000)) and nominal and row.get('voltage_v') is not None:
        vdev = (number(row['voltage_v']) - number(nominal)) / number(nominal) * 100
    bases = {
        'kw': row.get('load_pct'),
        'kvar': _pct(row.get('reactive_power_total_kvar'), row.get('apparent_power_total_kva')),
        'pf': row.get('power_factor_total'), 'volt': vdev, 'amp': amp_pct,
        'i_unbal': row.get('current_unbalance_pct'), 'kva': kva_pct,
        'v_unbal': row.get('voltage_unbalance_pct'),
    }
    displays = {
        'kw': row.get('active_power_total_kw'), 'kvar': row.get('reactive_power_total_kvar'),
        'pf': row.get('power_factor_total'), 'volt': row.get('voltage_v'),
        'amp': row.get('current_a'), 'i_unbal': row.get('current_unbalance_pct'),
        'kva': row.get('apparent_power_total_kva'),
        'v_unbal': row.get('voltage_unbalance_pct'),
    }
    row['cells'] = {
        key: {'value': displays[key], 'basis': bases[key],
              'status': classify(abs(number(bases[key])) if key == 'volt' and bases[key] is not None
                                 else bases[key], bands[key],
                                 'desc' if key == 'pf' else 'asc')}
        for key in bases
    }
    row['bands'] = {key: cell['status'] for key, cell in row['cells'].items()}
    row['band'] = row['bands']['kw']
    return row


def _aggregate_member(member, start, end, shift='all'):
    if shift and shift != 'all':
        rows = fetch_sampled(member.dsn, member.table, start, end, 'shift',
                             ENERGY_METRICS, member.time_col)
        rows = [row for row in rows if in_shift(row.get('bucket'), shift)]
        aggregate = {key: _sum(rows, key) for key in ENERGY_METRICS}
    else:
        rows = fetch_bucketed(member.dsn, member.table, start, end, None,
                              ENERGY_METRICS, member.time_col)
        aggregate = rows[0] if rows else {}
    live = _member_live(member)
    aggregate.update(_member_identity(member))
    aggregate.update({'rated_kva': member.config.get('rated_kva'),
                      'rated_kw': member.config.get('rated_kw')})
    aggregate.update({'ts': live.get('ts'), 'active_kw': live.get('active_power_total_kw'),
                      'reactive_kvar': live.get('reactive_power_total_kvar'),
                      'apparent_kva': live.get('apparent_power_total_kva'),
                      'power_factor': live.get('power_factor_total'),
                      'voltage_v': live.get('voltage_v'), 'current_a': live.get('current_a')})
    phases = [live.get(f'thd_current_{p}_pct') for p in ('r', 'y', 'b')]
    aggregate['i_thd_pct'] = max((v for v in phases if v is not None),
                                 default=live.get('thd_compliance_i_avg'))
    return aggregate


def _timeline(ctx, flt, metrics):
    def one(member):
        rows = fetch_sampled(member.dsn, member.table, flt.start, flt.end,
                             flt.sampling, metrics, member.time_col)
        return member, [row for row in rows if in_shift(row.get('bucket'), flt.shift)]

    merged = {}
    for member, rows in _parallel(list(ctx.members), one):
        for row in rows:
            bucket = row.get('bucket')
            item = merged.setdefault(bucket, {'bucket': bucket, 'feeders': []})
            feeder = {**_member_identity(member),
                      **{key: row.get(key) for key in metrics}}
            item['feeders'].append(feeder)
    return [merged[key] for key in sorted(merged) if key]


def _event_bucket(flt):
    return flt.sampling if flt.sampling in {'hour', 'day', 'week', 'month'} else 'hour'


def _bucket_datetime(value):
    try:
        parsed = dt.datetime.fromisoformat(value)
    except (TypeError, ValueError):
        return None
    return parsed.replace(tzinfo=IST) if parsed.tzinfo is None else parsed


def _add_event_rows(timeline, rows, keys, sampling):
    """Roll exact event counts into the possibly wider display buckets."""
    for event in rows:
        event_at = _bucket_datetime(event.get('bucket'))
        if event_at is None:
            continue
        for point in timeline:
            start, end = bucket_window(point['bucket'], sampling)
            start, end = _bucket_datetime(start), _bucket_datetime(end)
            if start and end and start <= event_at < end:
                for key in keys:
                    point[key] = number(point.get(key)) + number(event.get(key))
                break


class EnergyDistributionConsumer(FilteredPanelConsumer):
    page = 'energy-distribution'
    policy = TimeFilterPolicy(allowed_ranges=ALL_RANGES, supports_sampling=False)

    def build_snapshot(self):
        flt = self.resolve_filter()
        rows = _parallel(list(self.ctx.members), lambda m: _aggregate_member(
            m, flt.start, flt.end, flt.shift))
        sources = [row for row in rows if row['role'] == 'incoming']
        consumers = [row for row in rows if row['role'] == 'outgoing']
        spares = [row for row in rows if row['role'] == 'spare']
        supplied = _sum(sources, 'active_kwh')
        consumed = _sum(consumers, 'active_kwh')
        gap = (supplied - consumed if supplied is not None and consumed is not None else None)
        hours = max((flt.end - flt.start).total_seconds() / 3600, 0)
        rated = number(self.ctx.config.get('rated_kva'))
        for row in rows:
            capacity = number(row.get('rated_kva')) * .9 * hours
            row['utilization_pct'] = _pct(row.get('active_kwh'), capacity)
            row['capacity_kwh'] = capacity if capacity > 0 else None
            row['status'] = (feeder_energy_status(row['utilization_pct'], row.get('active_kw'),
                              row.get('power_factor'), row.get('i_thd_pct'))
                             if row['role'] == 'outgoing' else 'idle')
        for row in consumers:
            row['share_pct'] = _pct(row.get('active_kwh'), consumed)
        loss_pct = max(0, _pct(gap, supplied) or 0) if gap is not None and supplied else None
        review = (gap is not None and supplied and
                  (-gap > .02 * supplied or (loss_pct is not None and loss_pct >= 3)))
        reconciliation_status = 'Review' if review else 'OK'
        panel_node = f'panel:{self.ctx.id}'
        nodes = ([{'id': f'mfm:{row["mfm_id"]}', 'name': row['name'], 'role': 'incoming'}
                  for row in sources] +
                 [{'id': panel_node, 'name': self.ctx.name, 'role': 'panel'}] +
                 [{'id': f'mfm:{row["mfm_id"]}', 'name': row['name'], 'role': 'outgoing'}
                  for row in consumers])
        links = ([{'source': f'mfm:{row["mfm_id"]}', 'target': panel_node,
                   'value_kwh': row.get('active_kwh')} for row in sources] +
                 [{'source': panel_node, 'target': f'mfm:{row["mfm_id"]}',
                   'value_kwh': row.get('active_kwh')} for row in consumers])
        return {
            **self.identity(), **flt.envelope(),
            'ts': max((row.get('ts') for row in rows if row.get('ts')), default=None),
            'widgets': {
                'sources': sources, 'consumers': consumers, 'spares': spares,
                'energy_flow': {'nodes': nodes, 'links': links},
                'reconciliation': {
                    'supplied_kwh': _round(supplied), 'consumed_kwh': _round(consumed),
                    'gap_kwh': _round(gap), 'efficiency_pct': _round(_pct(consumed, supplied)),
                    'window_hours': _round(hours),
                    'loss_pct': _round(loss_pct),
                    'meter_gap_status': reconciliation_status,
                    'reconciliation_status': reconciliation_status,
                    'utilization_pct': _round(_pct(consumed, rated * .9 * hours)),
                },
                'topology': {'panel': {'id': self.ctx.id, 'key': self.ctx.slug,
                                       'name': self.ctx.name},
                             'incoming': [r['mfm_id'] for r in sources],
                             'outgoing': [r['mfm_id'] for r in consumers],
                             'spare': [r['mfm_id'] for r in spares]},
            },
        }


class PanelRealTimeMonitoringConsumer(LivePanelConsumer):
    page = 'real-time-monitoring'

    def build_snapshot(self):
        rows = _parallel(list(self.ctx.members), lambda m: _heatmap_row(m, self.ctx.rtm_bands))
        active = [row for row in rows if row['role'] != 'spare']
        incoming = [row for row in rows if row['role'] == 'incoming']
        outgoing = [row for row in rows if row['role'] == 'outgoing']
        supplied, consumed = _sum(incoming, 'active_power_total_kw'), _sum(outgoing, 'active_power_total_kw')

        def trend(member):
            samples = fetch_window(member.dsn, member.table, ['active_power_total_kw'],
                                   900, member.time_col)
            return member, samples
        merged = {}
        for member, samples in _parallel(_role_members(self.ctx, 'outgoing'), trend):
            for sample in samples:
                parsed = parse_timestamp(sample.get('ts'))
                if not parsed:
                    continue
                bucket = parsed.replace(second=0, microsecond=0).isoformat()
                point = merged.setdefault(bucket, {'ts': bucket, 'active_kw': 0, 'feeders': {}})
                value = number(sample.get('active_power_total_kw'))
                point['active_kw'] += value
                point['feeders'][str(member.eid)] = value
        selected = next((row for row in rows
                         if str(row['mfm_id']) == str(self.selected_feeder) or
                         str(row['mfm_id'] - 1_000_000) == str(self.selected_feeder)), None)
        return {
            **self.identity(),
            'ts': max((row.get('ts') for row in rows if row.get('ts')), default=None),
            'window_seconds': 900,
            'thresholds': self.ctx.rtm_bands,
            'widgets': {
                'heatmap': active,
                'power_balance': {'supplied_kw': _round(supplied),
                                  'consumed_kw': _round(consumed),
                                  'balance_kw': _round(number(supplied) - number(consumed))
                                  if supplied is not None and consumed is not None else None},
                'consumption_trend': [merged[key] for key in sorted(merged)],
                'bus_kpis': {
                    'peak_load_kw': max((number(r.get('active_power_total_kw')) for r in outgoing), default=None),
                    'power_factor': average(*(r.get('power_factor_total') for r in outgoing)),
                    'voltage_v': average(*(r.get('voltage_v') for r in outgoing)),
                    'worst_current_unbalance_pct': max((number(r.get('current_unbalance_pct')) for r in outgoing), default=None),
                    'total_current_a': _sum(outgoing, 'current_a'),
                },
                'selection': {'mfm_id': self.selected_feeder, 'feeder': selected},
            },
        }


class PanelEnergyPowerConsumer(FilteredPanelConsumer):
    page = 'energy-power'

    def build_snapshot(self):
        flt = self.resolve_filter()
        metrics = {**ENERGY_METRICS, **POWER_METRICS}
        timeline = _timeline(self.ctx, flt, metrics)
        for point in timeline:
            for key in metrics:
                energy_rows = ([row for row in point['feeders'] if row['role'] == 'outgoing']
                               if key in ENERGY_METRICS else point['feeders'])
                point[key] = _sum(energy_rows, key)
            point['label'] = bucket_label(point['bucket'], flt.sampling,
                                          (flt.end - flt.start).days > 1)
        period = _parallel(list(self.ctx.members), lambda m: _aggregate_member(
            m, flt.start, flt.end, flt.shift))
        live = _parallel(list(self.ctx.members), _member_live)
        outgoing = [row for row in live if row['role'] == 'outgoing']
        period_outgoing = [r for r in period if r['role'] == 'outgoing']
        total_kwh = _sum(period_outgoing, 'active_kwh')
        rated_kw = number(self.ctx.config.get('rated_kw'))
        live_kw = _sum(outgoing, 'active_power_total_kw')
        demands = []
        for point in timeline:
            demands.append({'bucket': point['bucket'], 'label': point['label'],
                            'feeders': [{'mfm_id': f['mfm_id'], 'name': f['name'],
                                         'peak_kw': f.get('active_kw_max')}
                                        for f in point['feeders']],
                            'peak_kw': point.get('active_kw_max')})
        return {
            **self.identity(), **flt.envelope(),
            'ts': max((row.get('ts') for row in live if row.get('ts')), default=None),
            'widgets': {
                'cumulative_energy': {
                    'active_kwh': _round(total_kwh),
                    'reactive_kvarh': _round(_sum(period_outgoing, 'reactive_kvarh')),
                    'apparent_kvah': _round(_sum(period_outgoing, 'apparent_kvah')),
                    'limit_mvah': self.ctx.config.get('subsidy_limit_mvah'),
                },
                'energy_trend': timeline,
                'live_power_analysis': {
                    'active_kw': _round(live_kw),
                    'reactive_kvar': _round(_sum(outgoing, 'reactive_power_total_kvar')),
                    'apparent_kva': _round(_sum(outgoing, 'apparent_power_total_kva')),
                    'rated_kw': rated_kw, 'load_pct': _round(_pct(live_kw, rated_kw)),
                    'rated_kva': self.ctx.config.get('rated_kva'),
                    'contracted_kw': self.ctx.config.get('contracted_kw'),
                    'critical_load_kw': self.ctx.config.get('critical_load_kw'),
                },
                'power_demand': demands,
            },
        }


VC_METRICS = {
    'voltage_v': ('avg', 'voltage_avg'), 'voltage_deviation_pct': ('max', 'kpi_voltage_deviation_pct'),
    'voltage_deviation_min_pct': ('min', 'kpi_voltage_deviation_pct'),
    'voltage_peak': ('max', 'voltage_avg'), 'voltage_low': ('min', 'voltage_avg'),
    'current_a': ('avg', 'current_avg'), 'current_unbalance_pct': ('max', 'current_unbalance_pct'),
    'neutral_a': ('max', 'current_neutral'), 'sag': ('max', 'sag_event_active'),
    'swell': ('max', 'swell_event_active'),
}


class PanelVoltageCurrentConsumer(FilteredPanelConsumer):
    page = 'voltage-current'

    def _detail_window(self, flt, timeline):
        if not self.selected_bucket:
            return flt.start, flt.end, None
        match = next((row['bucket'] for row in timeline
                      if row['bucket'] == self.selected_bucket or
                      bucket_label(row['bucket'], flt.sampling) == self.selected_bucket), None)
        start, end = bucket_window(match, flt.sampling)
        if not start or not end:
            return flt.start, flt.end, None
        return dt.datetime.fromisoformat(start), dt.datetime.fromisoformat(end), match

    def build_snapshot(self):
        flt = self.resolve_filter()
        timeline = _timeline(self.ctx, flt, VC_METRICS)
        by_eid = {m.eid: m for m in self.ctx.members}
        for point in timeline:
            for feeder in point['feeders']:
                member = by_eid[feeder['mfm_id']]
                voltage_window(feeder, member.config, member.dsn)
            point.update({
                'label': bucket_label(point['bucket'], flt.sampling,
                                      (flt.end - flt.start).days > 1),
                'sag_events': 0, 'swell_events': 0,
                'current_events': 0, 'neutral_events': 0,
                'worst_voltage_deviation_pct': max((abs(number(f.get('voltage_deviation_pct')))
                                                     for f in point['feeders']), default=None),
                'worst_current_unbalance_pct': max((number(f.get('current_unbalance_pct'))
                                                     for f in point['feeders']), default=None),
            })

        def event_rows(member):
            flags = fetch_event_counts(
                member.dsn, member.table, flt.start, flt.end, _event_bucket(flt),
                ['neutral_stress_event_active'], member.time_col)
            specs = {
                **voltage_events(member.config, member.dsn),
                'current_events': ('current_unbalance_pct', 'above', 10),
            }
            thresholds = fetch_threshold_events(
                member.dsn, member.table, flt.start, flt.end, _event_bucket(flt),
                specs, member.time_col)
            return flags, thresholds
        for flags, thresholds in _parallel(list(self.ctx.members), event_rows):
            renamed = [{**row, 'neutral_events': row.get('neutral_stress_event_active')} for row in flags]
            _add_event_rows(timeline, renamed, ('neutral_events',), flt.sampling)
            _add_event_rows(timeline, thresholds, ('sag_events', 'swell_events', 'current_events'),
                            flt.sampling)
        detail_start, detail_end, selected = self._detail_window(flt, timeline)

        def detail(member):
            rows = fetch_bucketed(member.dsn, member.table, detail_start, detail_end,
                                  None, VC_METRICS, member.time_col)
            row = rows[0] if rows else {}
            voltage_window(row, member.config, member.dsn)
            events = fetch_threshold_events(member.dsn, member.table, detail_start, detail_end,
                                            None, voltage_events(member.config, member.dsn), member.time_col)
            event = events[0] if events else {}
            row['sag_event_active'] = event.get('sag_events', 0) if row['online'] else 0
            row['swell_event_active'] = event.get('swell_events', 0) if row['online'] else 0
            row.update(_member_identity(member))
            row['highlight'] = 'success' if not row['online'] else panel_vc_highlight(
                row.get('voltage_deviation_pct'), row.get('current_unbalance_pct'),
                self.ctx.config)
            # Preserve the existing frontend vocabulary; expose the spec label too.
            row['status'] = {'success': 'normal', 'warn': 'warning', 'danger': 'danger'}[row['highlight']]
            return row
        details = _parallel([m for m in self.ctx.members if m.role != 'spare'], detail)
        return {
            **self.identity(), **flt.envelope(), 'selected_bucket': selected,
            'ts': self.latest_anchor().isoformat(),
            'widgets': {
                'event_summary': {
                    'sag': sum(p['sag_events'] for p in timeline),
                    'swell': sum(p['swell_events'] for p in timeline),
                    'current_unbalance': sum(p['current_events'] for p in timeline),
                    'neutral_stress': sum(p['neutral_events'] for p in timeline),
                    'worst_voltage_deviation_pct': max((p['worst_voltage_deviation_pct'] or 0 for p in timeline), default=None),
                    'worst_current_unbalance_pct': max((p['worst_current_unbalance_pct'] or 0 for p in timeline), default=None),
                },
                'event_timeline': timeline,
                'current_distribution': [{'mfm_id': r['mfm_id'], 'name': r['name'],
                                          'current_a': r.get('current_a')} for r in details],
                'feeder_table': details,
                'selection': {'mfm_id': self.selected_feeder, 'bucket': selected},
            },
        }


PQ_METRICS = {
    'i_thd_pct': ('max', 'thd_compliance_i_avg'), 'v_thd_pct': ('max', 'thd_compliance_v_avg'),
    'true_pf': ('avg', 'kpi_true_pf'), 'displacement_pf': ('avg', 'kpi_displacement_pf'),
    'neutral_a': ('max', 'current_neutral'), 'h5_pct': ('avg', 'harmonic_5_pct'),
    'h7_pct': ('avg', 'harmonic_7_pct'), 'k_factor': ('avg', 'k_factor'),
}


class PanelPowerQualityConsumer(FilteredPanelConsumer):
    page = 'power-quality-summary'

    def build_snapshot(self):
        flt = self.resolve_filter()
        timeline = _timeline(self.ctx, flt, PQ_METRICS)
        by_eid = {m.eid: m for m in self.ctx.members}
        for point in timeline:
            for feeder in point['feeders']:
                cfg = self.ctx.config
                feeder['i_thd_issue'] = number(feeder.get('i_thd_pct')) > number(cfg.get('thd_i_limit_pct'), 8)
                feeder['v_thd_issue'] = number(feeder.get('v_thd_pct')) > number(cfg.get('thd_v_limit_pct'), 5)
                feeder['pf_issue'] = (feeder.get('true_pf') is not None and
                                      number(feeder['true_pf']) < number(cfg.get('true_pf_min'), .9))
                feeder['neutral_issue'] = number(feeder.get('neutral_a')) > number(cfg.get('neutral_current_limit_a'))
            point.update({
                'label': bucket_label(point['bucket'], flt.sampling,
                                      (flt.end - flt.start).days > 1),
                'i_thd_issues': 0, 'v_thd_issues': 0,
                'pf_gap_issues': 0, 'neutral_issues': 0,
                'worst_i_thd_pct': max((number(f.get('i_thd_pct')) for f in point['feeders']), default=None),
                'worst_v_thd_pct': max((number(f.get('v_thd_pct')) for f in point['feeders']), default=None),
            })

        def pq_events(member):
            cfg = self.ctx.config
            return fetch_threshold_events(member.dsn, member.table, flt.start, flt.end,
                                          _event_bucket(flt), {
                'i_thd_issues': ('thd_compliance_i_avg', 'above',
                                 number(cfg.get('thd_i_limit_pct'), 8)),
                'v_thd_issues': ('thd_compliance_v_avg', 'above',
                                 number(cfg.get('thd_v_limit_pct'), 5)),
                'pf_gap_issues': ('kpi_true_pf', 'below',
                                  number(cfg.get('true_pf_min'), .9)),
                'neutral_issues': ('current_neutral', 'above',
                                   number(cfg.get('neutral_current_limit_a'))),
            }, member.time_col)
        issue_keys = ('i_thd_issues', 'v_thd_issues', 'pf_gap_issues', 'neutral_issues')
        for event_rows in _parallel(list(self.ctx.members), pq_events):
            _add_event_rows(timeline, event_rows, issue_keys, flt.sampling)
        latest = _parallel([m for m in self.ctx.members if m.role != 'spare'], _member_live)
        rows = []
        for row in latest:
            cfg = self.ctx.config
            ithd, vthd = row.get('thd_compliance_i_avg'), row.get('thd_compliance_v_avg')
            true_pf = (row.get('kpi_true_pf') if row.get('kpi_true_pf') is not None
                       else row.get('power_factor_total'))
            score = (4 * number(ithd) + 3 * number(vthd) +
                     2 * number(row.get('harmonic_5_pct')) + 2 * number(row.get('harmonic_7_pct')) +
                     (200 * max(0, number(cfg.get('true_pf_min'), .9) - true_pf)
                      if true_pf is not None else 0))
            severity = panel_pq_severity(ithd, true_pf, cfg)
            rows.append({**_member_identity(by_eid[row['mfm_id']]), 'ts': row.get('ts'),
                         'i_thd_pct': ithd, 'v_thd_pct': vthd, 'true_pf': true_pf,
                         'h5_pct': row.get('harmonic_5_pct'), 'h7_pct': row.get('harmonic_7_pct'),
                         'k_factor': row.get('k_factor'), 'score': _round(score),
                         'threshold_severity': severity,
                         'severity': {'danger': 'critical', 'warning': 'warning', 'success': 'normal'}[severity]})
        rows.sort(key=lambda row: row['score'], reverse=True)
        selected = next((row for row in rows
                         if str(row['mfm_id']) == str(self.selected_feeder) or
                         str(row['mfm_id'] - 1_000_000) == str(self.selected_feeder)),
                        rows[0] if rows else None)
        axes = ('i_thd_pct', 'v_thd_pct', 'h5_pct', 'h7_pct', 'k_factor')
        fleet = {key: average(*(row.get(key) for row in rows)) for key in axes}
        fleet['pf_gap'] = average(*(1 - number(row.get('true_pf')) for row in rows))
        signature = ({key: selected.get(key) for key in axes} if selected else {})
        signature['pf_gap'] = (1 - number(selected.get('true_pf')) if selected else None)
        return {
            **self.identity(), **flt.envelope(),
            'ts': max((row.get('ts') for row in rows if row.get('ts')), default=None),
            'widgets': {
                'issue_summary': {
                    'i_thd': sum(p['i_thd_issues'] for p in timeline),
                    'v_thd': sum(p['v_thd_issues'] for p in timeline),
                    'pf_gap': sum(p['pf_gap_issues'] for p in timeline),
                    'neutral_stress': sum(p['neutral_issues'] for p in timeline),
                    'worst_i_thd_pct': max((p['worst_i_thd_pct'] or 0 for p in timeline), default=None),
                    'worst_v_thd_pct': max((p['worst_v_thd_pct'] or 0 for p in timeline), default=None),
                },
                'timeline': timeline, 'feeder_table': rows,
                'signature': {'selected': selected, 'selected_values': signature,
                              'fleet_average': fleet},
                'selection': {'mfm_id': selected.get('mfm_id') if selected else None,
                              'matrix_focus': self.matrix_focus},
                'ai_summary': None,
            },
        }


def _register(name, consumer):
    existing = SCREENS.get(name, {})
    screen(name, distribution_panel=consumer,
           transformer=existing.get('transformer'), lt_panel=existing.get('lt_panel'))


for _name, _consumer in (
    ('energy-distribution', EnergyDistributionConsumer),
    ('real-time-monitoring', PanelRealTimeMonitoringConsumer),
    ('energy-power', PanelEnergyPowerConsumer),
    ('voltage-current', PanelVoltageCurrentConsumer),
    ('power-quality-summary', PanelPowerQualityConsumer),
):
    _register(_name, _consumer)
