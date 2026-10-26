"""Single-MFM WebSocket endpoints documented in ``single_panel_apis.md``."""
from __future__ import annotations

import datetime as dt
import math

from core.services import (bucket_label, fetch_bucketed, fetch_event_counts,
                           fetch_live, fetch_sampled, fetch_threshold_events,
                           fetch_window, grid_fill, hour_of_day_profile, in_shift,
                           peak_with_time)
from core.time_filters import ALL_RANGES, TimeFilterPolicy
from core.constants import FEEDER_ID_OFFSET
from core.models import MFM
from core.thresholds import (band, demand_band, pq_state, setting,
                             flicker_tone, crest_tone, trend_status)
from panels.consumers.base import (FilteredMFMConsumer, QueueMFMConsumer,
                                   SingleMFMConsumer, average, number,
                                   parse_timestamp)
from panels.registry import screen
from panels.consumers.derived import voltage_nominal, voltage_events, WindowMetrics


POWER = ('active_power_total_kw', 'reactive_power_total_kvar',
         'apparent_power_total_kva')
ENERGY = ('active_energy_import_kwh', 'reactive_energy_import_kvarh',
          'apparent_energy_kvah')
VOLTAGE = ('voltage_r_n', 'voltage_y_n', 'voltage_b_n', 'voltage_avg',
           'voltage_max', 'voltage_min', 'voltage_ry', 'voltage_yb',
           'voltage_br', 'voltage_unbalance_pct', 'kpi_voltage_deviation_pct')
CURRENT = ('current_r', 'current_y', 'current_b', 'current_neutral', 'current_avg',
           'current_max', 'current_min', 'current_unbalance_pct',
           'current_max_spread', 'current_spread_br', 'current_spread_ry',
           'current_spread_by', 'kpi_neutral_to_phase_ratio_pct')
PF = ('power_factor_r', 'power_factor_y', 'power_factor_b', 'power_factor_total',
      'kpi_displacement_pf', 'kpi_true_pf', 'phase_angle_deg')
THD = ('thd_voltage_r_pct', 'thd_voltage_y_pct', 'thd_voltage_b_pct',
       'thd_current_r_pct', 'thd_current_y_pct', 'thd_current_b_pct',
       'thd_compliance_v_avg', 'thd_compliance_i_avg', 'thd_compliance_ieee519')


def _round(value, digits=2):
    return round(float(value), digits) if value is not None else None


def _pct(value, basis):
    return (number(value) / number(basis) * 100.0) if number(basis) else None


def _worst(values):
    values = [abs(number(v)) for v in values if v is not None]
    return max(values) if values else None


def _status_high(value, watch, critical, normal='Normal', elevated='Elevated', high='Critical'):
    return band(value, watch, critical, (normal, elevated, high))


def _latest_anchor(row):
    return parse_timestamp((row or {}).get('ts')) or dt.datetime.now(dt.timezone.utc)


def _period_aggregate(ctx, start, end):
    rows = fetch_bucketed(ctx.dsn, ctx.table, start, end, None, {
        'active_kwh': ('delta', 'active_energy_import_kwh'),
        'reactive_kvarh': ('delta', 'reactive_energy_import_kvarh'),
        'apparent_kvah': ('delta', 'apparent_energy_kvah'),
        'active_kw_avg': ('avg', 'active_power_total_kw'),
        'active_kw_max': ('max', 'active_power_total_kw'),
        'reactive_kvar_avg': ('avg', 'reactive_power_total_kvar'),
        'apparent_kva_avg': ('avg', 'apparent_power_total_kva'),
    }, ctx.time_col)
    return rows[0] if rows else {}


def _related_binding(mfm):
    if mfm.data_source_id is None or not mfm.table_name:
        return None
    return {
        'id': FEEDER_ID_OFFSET + mfm.id, 'name': mfm.name,
        'dsn': mfm.data_source.dsn, 'table': mfm.table_name,
        'time_col': (mfm.logger_schema.timestamp_column
                     if mfm.logger_schema_id else 'timestamp'),
    }


def _related_energy(binding, start, end):
    if binding is None:
        return None
    rows = fetch_bucketed(binding['dsn'], binding['table'], start, end, None, {
        'value': ('delta', 'active_energy_import_kwh'),
    }, binding['time_col'])
    return rows[0].get('value') if rows else None


def _topology(ctx, start, end):
    """Panel membership topology, resolving every related MFM independently."""
    mfm = (MFM.objects.filter(pk=ctx.id)
           .select_related('reference')
           .prefetch_related('memberships__group').first())
    if mfm is None:
        return {'mode': None, 'panel': None, 'inputs': [], 'outputs': []}
    membership = (mfm.memberships.filter(group__is_panel=True, group__active=True)
                  .select_related('group').order_by('position', 'id').first())
    panel = mfm.reference if mfm.reference_id else (membership.group if membership else None)
    if panel is None:
        return {'mode': None, 'panel': None, 'inputs': [], 'outputs': []}
    members = (MFM.objects.filter(memberships__group=panel, active=True)
               .select_related('data_source', 'logger_schema')
               .prefetch_related('memberships').distinct())
    role_by_id = dict(panel.memberships.values_list('mfm_id', 'role'))
    inputs, outputs = [], []
    for related in members:
        binding = _related_binding(related)
        if binding is None:
            continue
        binding['energy_kwh'] = _related_energy(binding, start, end)
        live = fetch_live(binding['dsn'], binding['table'], ['active_power_total_kw'],
                          binding['time_col']) or {}
        binding['active_kw'] = number(live.get('active_power_total_kw'), None)
        role = role_by_id.get(related.id)
        (inputs if role == 'incoming' else outputs if role == 'outgoing' else []).append(binding)
    own_role = role_by_id.get(mfm.id)
    return {
        'mode': ('feeder_share' if own_role in {'outgoing', 'spare'} else
                 'input_output' if own_role == 'incoming' else None),
        'panel': {'id': panel.id, 'key': panel.slug, 'name': panel.name},
        'inputs': inputs, 'outputs': outputs,
    }


class OverviewConsumer(SingleMFMConsumer):
    page = 'overview'

    def build_snapshot(self):
        cols = (*POWER, *ENERGY, *VOLTAGE, *CURRENT, *PF, 'frequency_hz',
                'present_demand_kw', 'peak_demand_kw', 'busbar_temperature_c',
                'breaker_state', 'breaker_trip_count')
        live = fetch_live(self.ctx.dsn, self.ctx.table, cols, self.ctx.time_col) or {}
        live = {key: (value if key in {'ts', 'breaker_state'} else number(value, None))
                for key, value in live.items()}
        now = _latest_anchor(live)
        start = now.astimezone(dt.timezone(dt.timedelta(hours=5, minutes=30))) \
            .replace(hour=0, minute=0, second=0, microsecond=0).astimezone(dt.timezone.utc)
        today = _period_aggregate(self.ctx, start, now)
        cfg = self.ctx.config
        rated_kw, rated_kva = cfg.get('rated_kw'), cfg.get('rated_kva')
        target = cfg.get('energy_target_kwh_today')
        active = live.get('active_power_total_kw')
        load_pct = _pct(abs(active), rated_kw) if active is not None else None
        # This logger's voltage_avg is L-L. Compare like bases, never to L-N.
        nominal = voltage_nominal(cfg, self.ctx.dsn)
        voltage = live.get('voltage_avg')
        if voltage is None:
            nominal = cfg.get('nominal_voltage_ln_v') or cfg.get('nominal_voltage_v')
            voltage = average(live.get('voltage_r_n'), live.get('voltage_y_n'), live.get('voltage_b_n'))
        offline = voltage is None or voltage < (nominal * .5 if nominal else 100)
        vdev = ((number(voltage) - number(nominal)) / number(nominal) * 100
                if nominal and voltage is not None and not offline else None)
        currents = [live.get(key) for key in ('current_r', 'current_y', 'current_b')]
        current_avg = live.get('current_avg') or average(*currents)
        current_unbalance = live.get('current_unbalance_pct')
        if current_unbalance is None and current_avg:
            current_unbalance = (max(map(number, currents)) - min(map(number, currents))) \
                / number(current_avg) * 100
        rated_a = number(cfg.get('rated_current_a'))
        deadband = max(rated_a * 0.01, 1.0)
        pf = live.get('power_factor_total')
        displacement = live.get('kpi_displacement_pf')
        true_pf = live.get('kpi_true_pf')
        freq = live.get('frequency_hz')
        freq_nominal = setting(cfg, 'overview_freq_nominal_hz', 50)
        active_kwh = today.get('active_kwh')
        widgets = {
            'busbar_temperature': {
                'value_c': live.get('busbar_temperature_c'), 'rise_c': None,
                'status': (_status_high(live.get('busbar_temperature_c'),
                         setting(cfg, 'overview_busbar_temp_warn_c', 45),
                         setting(cfg, 'overview_busbar_temp_critical_c', 55),
                         'Normal', 'Warning')
                         if live.get('busbar_temperature_c') is not None else None),
            },
            'energy_consumption': {
                'today_kwh': _round(active_kwh), 'target_kwh': target,
                'pct_of_target': _round(_pct(active_kwh, target)),
                'today_reactive_kvarh': _round(today.get('reactive_kvarh')),
                'today_apparent_kvah': _round(today.get('apparent_kvah')),
                'budget_delta_kwh': (_round(number(active_kwh) - number(target))
                                     if active_kwh is not None and target else None),
                'status': _status_high(
                    _pct(active_kwh, target), cfg.get('overview_energy_elevated_pct', 75),
                    cfg.get('overview_energy_critical_pct', 90), 'On Track', 'Elevated'),
            },
            'kw_load': {
                'pct': _round(load_pct), 'active_kw': active, 'rated_kw': rated_kw,
                'rated_kva': rated_kva, 'reactive_kvar': live.get('reactive_power_total_kvar'),
                'apparent_kva': live.get('apparent_power_total_kva'),
                'load_factor': (_round(number(today.get('active_kw_avg')) /
                               number(today.get('active_kw_max')) * 100)
                                if today.get('active_kw_max') else None),
                'status': _status_high(
                    load_pct, cfg.get('overview_load_elevated_pct', 75),
                    cfg.get('overview_load_critical_pct', 90)),
            },
            'power_factor': {
                'value': pf, 'displacement_pf': displacement, 'true_pf': true_pf,
                'harmonic_gap': (_round(number(displacement) - number(true_pf), 3)
                                 if displacement is not None and true_pf is not None else None),
                'phases': {p: live.get(f'power_factor_{p}') for p in ('r', 'y', 'b')},
                'status': ('Good' if number(pf) >= number(cfg.get('overview_pf_good', .95))
                           else 'Fair' if number(pf) >= number(cfg.get('overview_pf_fair', .9))
                           else 'Poor'),
            },
            'voltage_deviation': {
                'pct': _round(vdev), 'actual_v': voltage, 'nominal_v': nominal,
                'limit_pct': cfg.get('voltage_statutory_deviation_pct'),
                'phases': {p: live.get(f'voltage_{p}_deviation_pct') for p in ('r', 'y', 'b')},
                'phase_v': {p: live.get(f'voltage_{p}_n') for p in ('r', 'y', 'b')},
                'status': 'Offline' if offline else _status_high(
                    abs(vdev) if vdev is not None else None, cfg.get('overview_voltage_dev_elevated_pct', 3),
                    cfg.get('overview_voltage_dev_critical_pct', 5)),
            },
            'grid_frequency': {
                'hz': freq, 'deviation_hz': _round(freq - freq_nominal, 3) if freq is not None else None,
                'nominal_hz': freq_nominal,
                'status': _status_high(
                    abs(freq - freq_nominal) if freq is not None else None,
                    cfg.get('overview_freq_fair_dev_hz', .05),
                    cfg.get('overview_freq_unstable_dev_hz', .1),
                    'Stable', 'Fair', 'Unstable'),
            },
            'phase_balance': {
                'pct': _round(current_unbalance), 'neutral_a': live.get('current_neutral'),
                'avg_phase_a': current_avg,
                **{p: live.get(f'current_{p}') for p in ('r', 'y', 'b')},
                'deviation': {p: live.get(f'current_{p}_deviation_pct')
                              for p in ('r', 'y', 'b')},
                'status': ('Idle' if number(current_avg) < deadband else _status_high(
                    current_unbalance, cfg.get('overview_phase_watch_pct', 5),
                    cfg.get('overview_phase_unbalanced_pct', 10),
                    'Balanced', 'Watch', 'Unbalanced')),
                'deadband_a': _round(deadband),
            },
            'breaker': {'state': live.get('breaker_state'),
                        'trips_24h': live.get('breaker_trip_count'),
                        'status': 'Healthy' if str(live.get('breaker_state')).lower() in {'closed', 'on'} else 'Check'},
            'demand_profile': {
                'peak_today_kw': _round(today.get('active_kw_max')),
                'max_kw': _round(today.get('active_kw_max')), 'daily_peaks_kva': [],
                'peak_this_month': None, 'monthly_avg_kva': None,
                'contract_limit_kva': rated_kva, 'excursion_days': [],
            },
            'phase_readings': {
                **{p.upper(): {'voltage_v': live.get(f'voltage_{p}_n'),
                               'current_a': live.get(f'current_{p}'),
                               'active_kw': live.get(f'active_power_{p}_kw'),
                               'pf': live.get(f'power_factor_{p}')}
                   for p in ('r', 'y', 'b')},
                'total': {
                    **{key: live.get(key)
                       for key in (*POWER, 'voltage_avg', 'current_avg')},
                    # The frontend reads total using the same leg keys as R/Y/B.
                    'voltage_v': live.get('voltage_avg'),
                    'current_a': live.get('current_avg'),
                },
                'line_voltages': {key: live.get(key)
                                  for key in ('voltage_ry', 'voltage_yb', 'voltage_br')},
            },
            'ai_summary': None,
        }
        return {**self.identity(), 'ts': live.get('ts'), 'widgets': widgets}


class RealTimeMonitoringConsumer(QueueMFMConsumer):
    page = 'real-time-monitoring'
    columns = (*POWER, *ENERGY, *VOLTAGE, *CURRENT)

    def shape_row(self, row):
        row = super().shape_row(row)
        row['voltage_ll_avg'] = row.get('voltage_ll_avg') or average(
            row.get('voltage_ry'), row.get('voltage_yb'), row.get('voltage_br'))
        return row


class EnergyPowerConsumer(QueueMFMConsumer):
    page = 'energy-power'
    columns = (*ENERGY, *POWER)

    def shape_row(self, row):
        row = super().shape_row(row)
        rated_kw = self.ctx.config.get('rated_kw')
        row['kpi_kw_load_pct_of_rated'] = _pct(row.get('active_power_total_kw'), rated_kw)
        row.update({
            'cumulative_vs_budget_kwh': None, 'hv_input_kw': None, 'lv_output_kw': None,
            'active_power_loss_kw': None, 'active_power_loss_pct': None,
            'loss_energy_today_kwh': None, 'specific_energy_consumption': None,
            'kpi_load_factor': None, 'peak_demand_today_kw': None,
            'peak_demand_at_time': None, 'power_rate_kw_per_h': None,
            'sec_trend_status': 'stable', 'load_factor_trend_status': 'stable',
            'peak_demand_trend_status': 'stable', 'power_trend_status': 'stable',
            'io_mode': None, 'io_input_mfm_id': None, 'io_input_mfm_name': None,
            'io_feeder_kwh': None, 'io_panel_incoming_kwh': None,
            'io_share_pct': None, 'io_panel': None, 'io_incomers': [],
            'output_energy_today_kwh': None,
        })
        row.update(getattr(self, 'period_values', {}))
        return row

    def build_snapshot(self):
        frame = super().build_snapshot()
        if not frame['queue']:
            return frame
        now = _latest_anchor(frame['queue'][-1])
        ist = now.astimezone(dt.timezone(dt.timedelta(hours=5, minutes=30)))
        day = ist.replace(hour=0, minute=0, second=0, microsecond=0).astimezone(dt.timezone.utc)
        week = (ist.replace(hour=0, minute=0, second=0, microsecond=0)
                - dt.timedelta(days=ist.weekday())).astimezone(dt.timezone.utc)
        month = ist.replace(day=1, hour=0, minute=0, second=0,
                            microsecond=0).astimezone(dt.timezone.utc)
        values = {}
        for label, start in {'today': day, 'this_week': week, 'this_month': month}.items():
            agg = _period_aggregate(self.ctx, start, now)
            values.update({
                f'active_energy_{label}_kwh': agg.get('active_kwh'),
                f'reactive_energy_{label}_kvarh': agg.get('reactive_kvarh'),
                f'apparent_energy_{label}_kvah': agg.get('apparent_kvah'),
            })
        topology = _topology(self.ctx, day, now)
        own_energy = values.get('active_energy_today_kwh')
        inputs, outputs = topology['inputs'], topology['outputs']
        input_energy = sum(number(item.get('energy_kwh')) for item in inputs) if inputs else None
        output_energy = sum(number(item.get('energy_kwh')) for item in outputs) if outputs else None
        input_power = sum(number(item.get('active_kw')) for item in inputs) if inputs else None
        output_power = sum(number(item.get('active_kw')) for item in outputs) if outputs else None
        loss_power = (input_power - output_power
                      if input_power is not None and output_power is not None else None)
        values.update({
            'io_mode': topology['mode'], 'io_panel': topology['panel'],
            'io_incomers': [{key: item.get(key) for key in ('id', 'name', 'energy_kwh')}
                             for item in inputs],
            'io_input_mfm_id': inputs[0]['id'] if inputs else None,
            'io_input_mfm_name': inputs[0]['name'] if inputs else None,
            'io_feeder_kwh': own_energy,
            'io_panel_incoming_kwh': input_energy,
            'io_share_pct': _pct(own_energy, input_energy),
            'output_energy_today_kwh': output_energy,
            'loss_energy_today_kwh': (number(input_energy) - number(output_energy)
                                      if input_energy is not None and output_energy is not None
                                      else None),
            'hv_input_kw': input_power, 'lv_output_kw': output_power,
            'active_power_loss_kw': loss_power,
            'active_power_loss_pct': _pct(loss_power, input_power),
        })
        sec = own_energy / output_energy if own_energy is not None and output_energy else None
        if getattr(self, 'sec_baseline', None) is None and sec is not None:
            self.sec_baseline = sec
        values['specific_energy_consumption'] = sec
        values['sec_trend_status'] = trend_status(sec, getattr(self, 'sec_baseline', None))
        peak, peak_at = peak_with_time(
            self.ctx.dsn, self.ctx.table, 'active_power_total_kw', day, now,
            self.ctx.time_col)
        values.update({'peak_demand_today_kw': peak, 'peak_demand_at_time': peak_at})
        self.period_values = values
        for row in frame['queue']:
            row.update(values)
        frame['status'] = self._status_map(frame['queue'][-1])
        return frame


class VoltageCurrentConsumer(QueueMFMConsumer):
    page = 'voltage-current'
    columns = (*VOLTAGE, *CURRENT, 'voltage_r_deviation_pct',
               'voltage_y_deviation_pct', 'voltage_b_deviation_pct',
               'current_r_deviation_pct', 'current_y_deviation_pct',
               'current_b_deviation_pct', 'sag_event_active', 'swell_event_active')

    def shape_row(self, row):
        row = super().shape_row(row)
        phases = [row.get(f'current_{p}') for p in ('r', 'y', 'b')]
        mean = row.get('current_avg') or average(*phases)
        deadband = max(number(self.ctx.config.get('rated_current_a')) * .01, 1.0)
        if row.get('current_unbalance_pct') is None and mean and number(mean) >= deadband:
            row['current_unbalance_pct'] = (max(map(number, phases)) - min(map(number, phases))) \
                / number(mean) * 100
        row['current_avg'] = mean
        row['voltage_ll_avg'] = row.get('voltage_ll_avg') or average(
            row.get('voltage_ry'), row.get('voltage_yb'), row.get('voltage_br'))
        row['current_deadband_a'] = deadband
        row['current_balance_status'] = ('Idle' if number(mean) < deadband else _status_high(
            row.get('current_unbalance_pct'),
            self.ctx.config.get('overview_phase_watch_pct', 5),
            self.ctx.config.get('overview_phase_unbalanced_pct', 10),
            'Balanced', 'Watch', 'Unbalanced'))
        if not hasattr(self, 'window_metrics'):
            self.window_metrics = WindowMetrics()
        row['events_24h'] = self.window_metrics.get(self.ctx, _latest_anchor(row))
        row.update({f'{key}_24h': value for key, value in row['events_24h'].items()})
        return row


class EnergySummaryConsumer(FilteredMFMConsumer):
    page = 'energy-summary'
    policy = TimeFilterPolicy(
        allowed_ranges=('today', 'yesterday', 'this_week', 'last_7d',
                        'this_month', 'last_month', 'custom'),
        supports_sampling=False, supports_shift=False)

    def build_snapshot(self):
        flt = self.resolve_filter()
        agg = _period_aggregate(self.ctx, flt.start, flt.end)
        active, reactive = agg.get('active_kwh'), agg.get('reactive_kvarh')
        apparent = agg.get('apparent_kvah')
        if apparent is None and (active is not None or reactive is not None):
            apparent = math.hypot(number(active), number(reactive))
        hours = (flt.full_end - flt.full_start).total_seconds() / 3600
        capacity = number(self.ctx.config.get('rated_kw')) * hours
        pct = _pct(active, capacity) if active is not None else None
        topology = _topology(self.ctx, flt.start, flt.end)
        downstream = sum(number(item.get('energy_kwh')) for item in topology['outputs'])
        sec = (number(active) / downstream
               if topology['mode'] == 'input_output' and downstream else None)
        if getattr(self, 'sec_baseline', None) is None and sec is not None:
            self.sec_baseline = sec
        metrics = [
            {'key': 'active', 'label': 'Active Energy', 'value': active,
             'unit': 'kWh', 'tone': 'normal', 'trend': 'stable'},
            {'key': 'reactive', 'label': 'Reactive Energy', 'value': reactive,
             'unit': 'kVArh', 'tone': 'normal', 'trend': 'stable'},
            {'key': 'apparent', 'label': 'Apparent Energy', 'value': apparent,
             'unit': 'kVAh', 'tone': 'normal', 'trend': 'stable'},
            {'key': 'sec', 'label': 'Specific Energy Consumption', 'value': None,
             'unit': 'kWh/kWh', 'tone': 'normal' if sec is not None else 'muted',
             'trend': trend_status(sec, getattr(self, 'sec_baseline', None))},
        ]
        metrics[-1]['value'] = sec
        return {
            **self.identity(), **flt.envelope(),
            'ts': flt.end.isoformat(),
            'headline': {'label': 'Apparent Energy', 'value': _round(apparent),
                         'unit': 'kVAh', 'target': _round(capacity),
                         'pct_of_target': _round(pct), 'meta': flt.range,
                         'tone': 'normal'},
            'progress': {'segments': [
                {'key': 'active', 'value': active},
                {'key': 'reactive', 'value': reactive},
            ], 'capacity': _round(capacity), 'marker_pct': _round(pct)},
            'metrics': metrics, 'insight': None,
        }


class PowerEnergyAnalysisConsumer(FilteredMFMConsumer):
    page = 'power-energy-analysis'

    def build_snapshot(self):
        flt = self.resolve_filter()
        rows = fetch_sampled(self.ctx.dsn, self.ctx.table, flt.start, flt.end,
                             flt.sampling, {
            'active': ('avg', 'active_power_total_kw'),
            'reactive': ('avg', 'reactive_power_total_kvar'),
            'apparent': ('avg', 'apparent_power_total_kva'),
            'demand': ('max', 'active_power_total_kw'),
        }, self.ctx.time_col)
        rows = [row for row in rows if in_shift(row.get('bucket'), flt.shift)]
        rated, contracted = self.ctx.config.get('rated_kw'), self.ctx.config.get('contracted_kw')
        bars, demand = [], []
        for row in rows:
            apparent = row.get('apparent')
            if apparent is None:
                apparent = math.hypot(number(row.get('active')), number(row.get('reactive')))
            label = bucket_label(row.get('bucket'), flt.sampling)
            bars.append({'time': label, 'active': row.get('active'),
                         'reactive': row.get('reactive'), 'apparent': apparent,
                         'shift': flt.shift, 'shiftTiming': row.get('bucket')})
            value = row.get('demand')
            legacy_band = 'critical' if contracted and value is not None and value > contracted else 'normal'
            demand.append({'time': label, 'value': value, 'band': legacy_band,
                           'threshold_band': demand_band(value, contracted),
                           'shift': flt.shift, 'shiftTiming': row.get('bucket')})
        vals = [number(row.get('active'), None) for row in rows if row.get('active') is not None]
        reactive = [number(row.get('reactive'), None) for row in rows if row.get('reactive') is not None]
        apparent = [number(item['apparent'], None) for item in bars if item['apparent'] is not None]
        return {
            **self.identity(), **flt.envelope(), 'ts': flt.end.isoformat(),
            'data': {
                'period': flt.range, 'periodOptions': list(flt.available_ranges),
                'sampling': flt.sampling,
                'availableSamplings': list(flt.available_samplings), 'view': 'power',
                'bars': bars, 'demandBars': demand, 'hourlyAverage': [],
                'ratedKw': rated, 'contractedKw': contracted,
                'activePowerAvgKw': average(*vals),
                'reactivePowerAvgKvar': average(*reactive),
                'apparentPowerAvgKva': average(*apparent),
                'yMin': min(vals) if vals else 0, 'yMax': max(vals) if vals else 0,
                'demandYMin': min((number(x['value']) for x in demand), default=0),
                'demandYMax': max((number(x['value']) for x in demand), default=0),
                'insight': None,
                'demandBandLegend': [
                    {'band': 'normal', 'label': 'Within contract',
                     'threshold': contracted, 'color': 'green'},
                    {'band': 'critical', 'label': 'Above contract',
                     'threshold': contracted, 'color': 'red'},
                ],
            },
        }


class LoadAnomaliesConsumer(FilteredMFMConsumer):
    page = 'load-anomalies'

    def build_snapshot(self):
        flt = self.resolve_filter()
        rows = fetch_sampled(self.ctx.dsn, self.ctx.table, flt.start, flt.end,
                             flt.sampling, {
            'avg_kw': ('avg', 'active_power_total_kw'),
            'min_kw': ('min', 'active_power_total_kw'),
            'max_kw': ('max', 'active_power_total_kw'),
        }, self.ctx.time_col)
        rows = [row for row in rows if in_shift(row.get('bucket'), flt.shift)]
        rated = number(self.ctx.config.get('rated_kw'))
        history_start = flt.start - dt.timedelta(days=30)
        profile = hour_of_day_profile(
            self.ctx.dsn, self.ctx.table, 'active_power_total_kw', history_start,
            flt.start, self.ctx.time_col, exclude_idle=True)
        threshold = setting(self.ctx.config, 'load_high_threshold_pct', 85.)
        # Align historical shape to the window level; retain percentile spread.
        window_mean = average(*(r.get('avg_kw') for r in rows))
        profile_mean = average(*(v[0] for v in profile.values()))
        alignment = window_mean / profile_mean if window_mean is not None and profile_mean else 1.
        actual, expected, ranges, anomalies = [], [], [], []
        for row in rows:
            stamp = row.get('bucket')
            label = bucket_label(stamp, flt.sampling)
            value = _pct(row.get('avg_kw'), rated) if row.get('avg_kw') is not None else None
            actual.append({'time': label, 'value': value, 'shift': flt.shift,
                           'shiftTiming': stamp})
            parsed = parse_timestamp(stamp)
            centre_kw, half_kw = profile.get(parsed.astimezone(
                dt.timezone(dt.timedelta(hours=5, minutes=30))).hour, (None, None)) \
                if parsed else (None, None)
            centre = min(150., _pct(centre_kw * alignment, rated)) if centre_kw is not None and rated else None
            half = min(20., max(5., _pct(abs(half_kw * alignment), rated) or 0)) if half_kw is not None else 5.
            low, high = (centre - half, centre + half) if centre is not None else (None, None)
            expected.append({'time': label, 'value': centre})
            ranges.append({'time': label, 'min': low, 'max': high})
            if value is None or not centre:
                continue
            peak_pct = _pct(row.get('max_kw'), rated) if row.get('max_kw') is not None else None
            if peak_pct is not None and peak_pct >= threshold:
                anomalies.append({'time': label, 'type': 'surge', 'value': peak_pct,
                                  'overload': True, 'title': 'Load overload'})
            elif value > high:
                anomalies.append({'time': label, 'type': 'surge', 'value': value, 'overload': False})
            elif value < low:
                anomalies.append({'time': label, 'type': 'dip', 'value': value, 'overload': False})
        values = [number(item['value']) for item in actual if item['value'] is not None]
        return {
            **self.identity(), **flt.envelope(), 'ts': flt.end.isoformat(),
            'data': {
                'period': flt.range, 'periodOptions': list(flt.available_ranges),
                'sampling': flt.sampling,
                'availableSamplings': list(flt.available_samplings),
                'actualLoad': actual, 'expectedLoad': expected, 'expectedRange': ranges,
                'anomalies': anomalies, 'maxThresholdPct': threshold,
                'presentValuePct': values[-1] if values else None,
                'surgeEvents': sum(x['type'] == 'surge' for x in anomalies),
                'dipEvents': sum(x['type'] == 'dip' for x in anomalies),
                'overloadEvents': sum(x['overload'] for x in anomalies),
                'patternSurgeEvents': sum(x['type'] == 'surge' and not x['overload'] for x in anomalies),
                'loadFactorPct': (average(*values) if values else None),
                'yMin': min(values) if values else 0, 'yMax': max(values) if values else 0,
                'insight': None, 'labels': {}, 'colors': {},
            },
        }


class VoltageHistoryConsumer(FilteredMFMConsumer):
    page = 'voltage-history'

    def build_snapshot(self):
        flt = self.resolve_filter()
        metrics = {
            **{f'voltage_{p}_n_avg': ('avg', f'voltage_{p}_n') for p in ('r', 'y', 'b')},
            **{f'voltage_{p}_avg': ('avg', f'voltage_{p}') for p in ('ry', 'yb', 'br')},
            'vdev_max': ('max', 'kpi_voltage_deviation_pct'),
            'vdev_min': ('min', 'kpi_voltage_deviation_pct'),
            'voltage_avg_max': ('max', 'voltage_avg'),
            'voltage_avg_min': ('min', 'voltage_avg'),
            'vmax': ('max', 'voltage_max'), 'vmin': ('min', 'voltage_min'),
        }
        rows = fetch_sampled(self.ctx.dsn, self.ctx.table, flt.start, flt.end,
                             flt.sampling, metrics, self.ctx.time_col)
        rows = [row for row in rows if in_shift(row.get('bucket'), flt.shift)]
        spreads = []
        for row in rows:
            nominal = voltage_nominal(self.ctx.config, self.ctx.dsn)
            if (self.ctx.config.get('nominal_voltage_ll_v') or 0) > 1000 and nominal:
                for target, source in (('vdev_max', 'voltage_avg_max'), ('vdev_min', 'voltage_avg_min')):
                    row[target] = ((number(row[source]) - nominal) / nominal * 100
                                   if row.get(source) is not None else None)
            vals = [(p, row.get(f'voltage_{p}_n_avg')) for p in ('r', 'y', 'b')]
            vals = [(p, number(v)) for p, v in vals if v is not None]
            if vals:
                lo, hi = min(vals, key=lambda x: x[1]), max(vals, key=lambda x: x[1])
                spreads.append((hi[1] - lo[1], f'{hi[0].upper()}-{lo[0].upper()}'))
            row['vamx'], row['vamn'] = row.get('vmax'), row.get('vmin')
        events = fetch_threshold_events(
            self.ctx.dsn, self.ctx.table, flt.start, flt.end, None,
            voltage_events(self.ctx.config, self.ctx.dsn), self.ctx.time_col)
        events = events[0] if events else {}
        worst = max(spreads, default=(None, None), key=lambda x: number(x[0]))
        deviations = [v for row in rows for v in (row.get('vdev_max'), row.get('vdev_min'))
                      if v is not None]
        return {
            **self.identity(), **flt.envelope(), 'columns': list(metrics),
            'count': len(rows), 'buckets': rows,
            'kpis': {
                'max_deviation_pct': _worst(deviations),
                'worst_spread_v': worst[0], 'worst_spread_pair': worst[1],
                'sag_events': events.get('sag_events', 0),
                'swell_events': events.get('swell_events', 0),
            },
        }


class CurrentHistoryConsumer(FilteredMFMConsumer):
    page = 'current-history'

    def build_snapshot(self):
        flt = self.resolve_filter()
        metrics = {
            **{f'current_{p}_avg': ('avg', f'current_{p}') for p in ('r', 'y', 'b')},
            'cmax': ('max', 'current_max'), 'cavg': ('avg', 'current_avg'),
            'cunb_max': ('max', 'current_unbalance_pct'),
            'cneut_max': ('max', 'current_neutral'),
        }
        rows = fetch_sampled(self.ctx.dsn, self.ctx.table, flt.start, flt.end,
                             flt.sampling, metrics, self.ctx.time_col)
        rows = [row for row in rows if in_shift(row.get('bucket'), flt.shift)]
        return {
            **self.identity(), **flt.envelope(), 'columns': list(metrics),
            'count': len(rows), 'buckets': rows,
            'kpis': {
                'peak_current_a': max((number(row.get('cmax')) for row in rows), default=None),
                'average_current_a': average(*(row.get('cavg') for row in rows)),
                'max_unbalance_pct': max((number(row.get('cunb_max')) for row in rows), default=None),
                'neutral_peak_a': max((number(row.get('cneut_max')) for row in rows), default=None),
            },
        }


class PowerQualitySummaryConsumer(SingleMFMConsumer):
    page = 'power-quality-summary'

    def build_snapshot(self):
        cols = (*THD, *PF, 'harmonic_5_pct', 'harmonic_7_pct', 'flicker_pst',
                'crest_factor_voltage', 'k_factor')
        live = fetch_live(self.ctx.dsn, self.ctx.table, cols, self.ctx.time_col) or {}
        live = {key: (value if key in {'ts', 'thd_compliance_ieee519'} else number(value, None))
                for key, value in live.items()}
        cfg = self.ctx.config
        iphases = {p: live.get(f'thd_current_{p}_pct') for p in ('r', 'y', 'b')}
        vphases = {p: live.get(f'thd_voltage_{p}_pct') for p in ('r', 'y', 'b')}
        ithd, vthd = _worst(iphases.values()), _worst(vphases.values())
        if ithd is None:
            ithd = live.get('thd_compliance_i_avg')
        if vthd is None:
            vthd = live.get('thd_compliance_v_avg')
        ilim = setting(cfg, 'thd_i_limit_pct', 8)
        vlim = setting(cfg, 'thd_v_limit_pct', 8)
        hlim = setting(cfg, 'ieee_519_individual_harmonic_limit_pct', 8)
        compliance, severity = pq_state(
            [ithd, vthd, live.get('harmonic_5_pct'), live.get('harmonic_7_pct')],
            [ilim, vlim, hlim, hlim], live.get('thd_compliance_ieee519'))
        constraint = ('current' if ithd is not None and ithd > number(ilim)
                      else 'voltage' if vthd is not None and vthd > number(vlim) else None)
        return {
            **self.identity(), 'ts': live.get('ts'),
            'widgets': {
                'feeder_pq_state': {
                    'ieee_state': compliance,
                    'ieee_constraint': constraint, 'trend': 'flat',
                    'severity': severity, 'limits_source': 'mfm_config',
                },
                'feeder_thd': {
                    'i_thd': {'value_pct': ithd, 'limit_pct': ilim,
                              'scale_max_pct': max(number(ilim) * 1.25, number(ithd)),
                              'phases': iphases},
                    'v_thd': {'value_pct': vthd, 'limit_pct': vlim,
                              'scale_max_pct': max(number(vlim) * 1.25, number(vthd)),
                              'phases': vphases},
                },
                'feeder_individual_harmonics': {
                    'h5': {'value_pct': live.get('harmonic_5_pct'),
                           'limit_pct': hlim, 'scale_max_pct': hlim * 1.25},
                    'h7': {'value_pct': live.get('harmonic_7_pct'),
                           'limit_pct': hlim, 'scale_max_pct': hlim * 1.25},
                },
                'feeder_voltage_quality': {
                    'flicker_pst': live.get('flicker_pst'),
                    'crest_factor': live.get('crest_factor_voltage'),
                    'k_factor': live.get('k_factor'),
                    'flicker_limit': setting(cfg, 'flicker_pst_limit', 1),
                    'flicker_tone': flicker_tone(live.get('flicker_pst'), setting(cfg, 'flicker_pst_limit', 1)),
                    'crest_factor_ideal': setting(cfg, 'crest_factor_ideal', 1.414),
                    'crest_tone': crest_tone(live.get('crest_factor_voltage'), setting(cfg, 'crest_factor_ideal', 1.414)),
                },
                'feeder_source_mitigation': {
                    'likely_source': None, 'filter_state': None,
                    'capacitor_bank': None,
                    'next_priority': {'High': 'Act now', 'Watch': 'Monitor', 'Low': '—'}[severity],
                    'next_priority_tone': {'High': 'critical', 'Watch': 'watch', 'Low': 'ok'}[severity],
                },
            },
        }


class PowerQualityHistoryConsumer(FilteredMFMConsumer):
    page = 'power-quality-history'

    def build_snapshot(self):
        flt = self.resolve_filter()
        metrics = {
            **{f'thd_voltage_{p}_pct_avg': ('avg', f'thd_voltage_{p}_pct')
               for p in ('r', 'y', 'b')},
            **{f'thd_current_{p}_pct_avg': ('avg', f'thd_current_{p}_pct')
               for p in ('r', 'y', 'b')},
            'voltage_avg_avg': ('avg', 'voltage_avg'),
            'thd_compliance_v_avg_avg': ('avg', 'thd_compliance_v_avg'),
            'thd_compliance_v_avg_max': ('max', 'thd_compliance_v_avg'),
            'thd_compliance_i_avg_avg': ('avg', 'thd_compliance_i_avg'),
            'thd_compliance_i_avg_max': ('max', 'thd_compliance_i_avg'),
            'power_factor_total_avg': ('avg', 'power_factor_total'),
            'power_factor_total_min': ('min', 'power_factor_total'),
            'kpi_true_pf_avg': ('avg', 'kpi_true_pf'),
            'kpi_true_pf_min': ('min', 'kpi_true_pf'),
            'kpi_displacement_pf_avg': ('avg', 'kpi_displacement_pf'),
            'phase_angle_deg_avg': ('avg', 'phase_angle_deg'),
            'k_factor_avg': ('avg', 'k_factor'), 'k_factor_max': ('max', 'k_factor'),
            'h5_avg': ('avg', 'harmonic_5_pct'), 'h7_avg': ('avg', 'harmonic_7_pct'),
        }
        rows = fetch_sampled(self.ctx.dsn, self.ctx.table, flt.start, flt.end,
                             flt.sampling, metrics, self.ctx.time_col)
        rows = [row for row in rows if in_shift(row.get('bucket'), flt.shift)]

        def vals(key):
            return [number(row[key]) for row in rows if row.get(key) is not None]

        vthd = [number(row.get(f'thd_voltage_{p}_pct_avg')) for row in rows
                for p in ('r', 'y', 'b') if row.get(f'thd_voltage_{p}_pct_avg') is not None]
        ithd = [number(row.get(f'thd_current_{p}_pct_avg')) for row in rows
                for p in ('r', 'y', 'b') if row.get(f'thd_current_{p}_pct_avg') is not None]
        gaps = [number(row.get('kpi_displacement_pf_avg')) - number(row.get('kpi_true_pf_avg'))
                for row in rows if row.get('kpi_displacement_pf_avg') is not None
                and row.get('kpi_true_pf_avg') is not None]
        return {
            **self.identity(), **flt.envelope(), 'columns': list(metrics),
            'count': len(rows), 'buckets': rows,
            'kpis': {
                'v_thd_avg': average(*vthd), 'i_thd_avg': average(*ithd),
                'v_thd_max': max(vthd, default=None), 'i_thd_max': max(ithd, default=None),
                'pf_avg': average(*vals('power_factor_total_avg')),
                'pf_min': min(vals('power_factor_total_min'), default=None),
                'true_pf_avg': average(*vals('kpi_true_pf_avg')),
                'true_pf_min': min(vals('kpi_true_pf_min'), default=None),
                'k_factor_max': max(vals('k_factor_max'), default=None),
                'pf_displacement_gap_avg': average(*gaps),
                'h5_avg': average(*vals('h5_avg')), 'h7_avg': average(*vals('h7_avg')),
            },
        }


# A single MFM has the same endpoint contract whether it represents a
# transformer feeder or an LT-panel feeder. Distribution panels are registered
# separately when their aggregate consumers are implemented.
for _name, _consumer in (
    ('overview', OverviewConsumer),
    ('real-time-monitoring', RealTimeMonitoringConsumer),
    ('energy-power', EnergyPowerConsumer),
    ('energy-summary', EnergySummaryConsumer),
    ('power-energy-analysis', PowerEnergyAnalysisConsumer),
    ('load-anomalies', LoadAnomaliesConsumer),
    ('voltage-current', VoltageCurrentConsumer),
    ('voltage-history', VoltageHistoryConsumer),
    ('current-history', CurrentHistoryConsumer),
    ('power-quality-summary', PowerQualitySummaryConsumer),
    ('power-quality-history', PowerQualityHistoryConsumer),
):
    screen(_name, transformer=_consumer, lt_panel=_consumer)
