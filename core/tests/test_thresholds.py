import datetime as dt
from types import SimpleNamespace
from unittest.mock import patch

from django.core.exceptions import ValidationError
from django.test import SimpleTestCase, TestCase

from core.config import resolve_mfm_config, resolve_panel_config
from core.models import (Equipment, EquipmentConfig, Group, MFM, MfmThreshold,
                         PanelConfig, PanelType, RtmThreshold)
from core.rtm import resolve_thresholds
from core.rtm_defaults import DEFAULT_BANDS, classify
from core.thresholds import (band, crest_tone, demand_band, feeder_energy_status,
                             flicker_tone, panel_pq_severity, panel_vc_highlight,
                             pq_state, setting, trend_status)
from panels.consumers.base import MeterContext, json_value
from panels.consumers.derived import voltage_nominal, voltage_events, voltage_window, WindowMetrics
from panels.consumers.distribution import _heatmap_row
from panels.consumers.single import OverviewConsumer, PowerQualitySummaryConsumer


class BoundaryTests(SimpleTestCase):
    def test_inclusive_overview_and_null(self):
        for value, expected in [(None, None), (3, 'Normal'), (5, 'Elevated'), (5.01, 'Critical')]:
            self.assertEqual(band(value, 3, 5), expected)
        self.assertEqual(setting({'x': 0}, 'x', 8), 0)
        self.assertEqual(setting({'x': None}, 'x', 8), 8)

    def test_rtm_boundaries(self):
        self.assertEqual(classify(95, DEFAULT_BANDS['kw'], 'asc'), 'critical')
        self.assertEqual(classify(.95, DEFAULT_BANDS['pf'], 'desc'), 'normal')

    def test_panel_scoped_vc_boundaries(self):
        self.assertEqual(panel_vc_highlight(-5, 5, {}), 'warn')
        self.assertEqual(panel_vc_highlight(-5.1, 5, {}), 'danger')
        self.assertEqual(panel_vc_highlight(5.1, 5, {'vc_vdev_danger_pct': 6}), 'warn')
        self.assertEqual(panel_vc_highlight(0, 8, {}), 'warn')

    def test_panel_pq_boundaries_and_no_fabricated_bad_pf(self):
        self.assertEqual(panel_pq_severity(6.4, None, {}), 'warning')
        self.assertEqual(panel_pq_severity(8, .95, {}), 'warning')
        self.assertEqual(panel_pq_severity(8.01, .95, {}), 'danger')
        self.assertEqual(panel_pq_severity(None, None, {}), 'success')

    def test_energy_gates(self):
        self.assertEqual(feeder_energy_status(None, .5, .1, 30), 'success')
        self.assertEqual(feeder_energy_status(85, .5, .1, 30), 'warning')
        self.assertEqual(feeder_energy_status(None, 10, 1.1, 15), 'danger')
        self.assertEqual(feeder_energy_status(None, 2, -.85, 30), 'warning')

    def test_demand_and_pq(self):
        self.assertEqual([demand_band(x, 100) for x in (None, 89, 90, 100, 101)],
                         ['low', 'low', 'moderate', 'moderate', 'high'])
        self.assertEqual(pq_state([8, 0, 0, 0], [8]*4), ('Pass', 'Watch'))
        self.assertEqual(pq_state([0, 0, 9, 0], [8]*4), ('Fail', 'High'))
        self.assertEqual(pq_state([9, 0, 0, 0], [8]*4, 'PASS'), ('Pass', 'Watch'))
        self.assertEqual(pq_state([0]*4, [8]*4, 'FAIL'), ('Fail', 'High'))

    def test_flicker_crest_and_trend(self):
        self.assertEqual(flicker_tone(.7, 1), 'watch')
        self.assertEqual(flicker_tone(1, 1), 'critical')
        self.assertEqual(crest_tone(1.6, 1.414), 'critical')
        self.assertEqual(trend_status(105, 100), 'stable')
        self.assertEqual(trend_status(106, 100), 'rising')
        self.assertIsNone(json_value(float('nan')))

    def test_voltage_source_bases_and_ht_events(self):
        cfg = {'nominal_voltage_ll_v': 33000, 'nominal_voltage_ln_v': 19052.6}
        self.assertEqual(voltage_nominal(cfg, 'dbname=target_version1'), 33000)
        self.assertEqual(voltage_nominal(cfg, 'dbname=legacy'), 19052.6)
        self.assertEqual(voltage_events(cfg, 'dbname=target_version1')['sag_events'],
                         ('voltage_avg', 'below', 29700))
        row = voltage_window({'voltage_peak': 33000, 'voltage_low': 32000,
                              'voltage_deviation_pct': 14000}, cfg, 'dbname=target_version1')
        self.assertAlmostEqual(row['voltage_deviation_pct'], 100/33)
        self.assertIsNone(voltage_window({'voltage_peak': 0}, cfg, 'dbname=target_version1')['voltage_deviation_pct'])

    @patch('panels.consumers.distribution._member_live')
    def test_heatmap_uses_resolved_bands_and_kva_for_current(self, live):
        member = SimpleNamespace(config={'rated_kva': 100, 'rated_current_a': 1000}, dsn='legacy')
        live.return_value = {'load_pct': 50, 'apparent_power_total_kva': 50, 'current_a': 1}
        custom = {**DEFAULT_BANDS, 'kw': (1, 2, 3, 4), 'amp': (1, 2, 3, 4)}
        row = _heatmap_row(member, custom)
        self.assertEqual(row['cells']['kw']['status'], 'critical')
        self.assertEqual(row['cells']['amp']['basis'], 50)
        self.assertEqual(row['band'], 'critical')

    @patch('panels.consumers.derived.fetch_threshold_events', return_value=[{'sag_events': 2}])
    def test_window_cache(self, fetch):
        ctx = SimpleNamespace(config={}, dsn='legacy', table='meter', time_col='timestamp')
        window = WindowMetrics()
        for _ in range(2):
            self.assertEqual(window.get(ctx, dt.datetime.now(dt.timezone.utc))['sag_events'], 2)
        self.assertEqual(fetch.call_count, 1)


class ResolutionTests(TestCase):
    def setUp(self):
        self.group = Group.objects.create(name='Threshold panel', slug='threshold-panel', is_panel=True)
        self.kind = PanelType.objects.get(code='distribution_panel')
        self.equipment = Equipment.objects.create(key='threshold-panel', name='Threshold panel',
                                                   group=self.group, panel_type=self.kind)

    def test_rtm_group_then_type_then_defaults(self):
        self.assertEqual(resolve_thresholds(self.group), DEFAULT_BANDS)
        RtmThreshold.objects.create(panel_type=self.kind, metric='kw', low_max=1,
                                    normal_max=2, moderate_max=3, high_max=4)
        self.assertEqual(resolve_thresholds(self.group)['kw'], (1, 2, 3, 4))
        RtmThreshold.objects.create(group=self.group, metric='kw', low_max=5,
                                    normal_max=6, moderate_max=7, high_max=8)
        self.assertEqual(resolve_thresholds(self.group)['kw'], (5, 6, 7, 8))
        self.assertEqual(resolve_thresholds(self.group)['pf'], DEFAULT_BANDS['pf'])

    def test_scope_constraint(self):
        row = RtmThreshold(group=self.group, panel_type=self.kind, metric='kw',
                           low_max=1, normal_max=2, moderate_max=3, high_max=4)
        with self.assertRaises(ValidationError):
            row.full_clean()

    def test_panel_precedence_and_meter_null_zero(self):
        EquipmentConfig.objects.create(equipment=self.equipment, vc_vdev_warn_pct=4)
        PanelConfig.objects.create(group=self.group, vc_vdev_danger_pct=9)
        cfg = resolve_panel_config(Group.objects.get(pk=self.group.pk))
        self.assertEqual((cfg['vc_vdev_warn_pct'], cfg['vc_vdev_danger_pct']), (4, 9))
        self.assertEqual(cfg['vc_iunbal_danger_pct'], 8)
        meter = MFM.objects.create(name='Threshold meter', slug='threshold-meter')
        MfmThreshold.objects.create(mfm=meter, overview_phase_watch_pct=0, v_thd_limit_pct=7)
        cfg = resolve_mfm_config(MFM.objects.get(pk=meter.pk))
        self.assertEqual(cfg['overview_phase_watch_pct'], 0)
        self.assertEqual(cfg['overview_load_elevated_pct'], 75)
        self.assertEqual(cfg['thd_v_limit_pct'], cfg['ieee_519_voltage_thd_limit_pct'])
        self.assertEqual(cfg['thd_v_limit_pct'], 7)


class SnapshotTests(SimpleTestCase):
    def ctx(self, config):
        return MeterContext(1, 1000001, 'Meter', 'meter', 'meter', 'lt_panel',
                            'dbname=target_version1', 'meter', 'timestamp', config)

    @patch('panels.consumers.single._period_aggregate', return_value={})
    @patch('panels.consumers.single.fetch_live')
    def test_overview_saved_limits_and_voltage(self, live, aggregate):
        live.return_value = {'voltage_avg': 415, 'active_power_total_kw': 80,
                             'busbar_temperature_c': 50, 'breaker_state': 'CLOSED'}
        consumer = OverviewConsumer()
        consumer.ctx = self.ctx({'nominal_voltage_ll_v': 415, 'nominal_voltage_ln_v': 239.6,
                                 'rated_kw': 100, 'overview_load_elevated_pct': 85,
                                 'overview_load_critical_pct': 95})
        widgets = consumer.build_snapshot()['widgets']
        self.assertEqual(widgets['voltage_deviation']['pct'], 0)
        self.assertEqual(widgets['kw_load']['status'], 'Normal')
        self.assertEqual(widgets['busbar_temperature']['status'], 'Warning')
        self.assertEqual(widgets['breaker']['status'], 'Healthy')
        self.assertIsNone(widgets['grid_frequency']['status'])

    @patch('panels.consumers.single.fetch_live')
    def test_pq_harmonics_and_explicit_verdict(self, live):
        consumer = PowerQualitySummaryConsumer()
        consumer.ctx = self.ctx({'ieee_519_individual_harmonic_limit_pct': 4})
        live.return_value = {'harmonic_5_pct': 5}
        widgets = consumer.build_snapshot()['widgets']
        self.assertEqual(widgets['feeder_pq_state']['severity'], 'High')
        live.return_value['thd_compliance_ieee519'] = 'PASS'
        self.assertEqual(consumer.build_snapshot()['widgets']['feeder_pq_state']['severity'], 'Watch')
