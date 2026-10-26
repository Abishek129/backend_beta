"""Config resolution: panel nominal stays L-L, meter nominal ships L-N,
ratings derive from the meter's own kVA, frontend key names are emitted."""
import math

from django.test import TestCase

from django.core.exceptions import ValidationError

from core.config import resolve_for_id, resolve_mfm_config, resolve_panel_config
from core.constants import FEEDER_ID_OFFSET
from core.models import (Breaker, Equipment, EquipmentConfig, Group, Membership,
                         MfmConfig, MFM, MfmThreshold, PanelConfig, PanelType)


class ConfigResolutionTests(TestCase):
    def setUp(self):
        self.panel = Group.objects.create(
            logger_id='grp_mcc1', name='MCC-1', slug='mcc-1', is_panel=True)
        self.mfm = MFM.objects.create(
            logger_id='dev_01', name='MFM-01', slug='mfm-01')

    # --- panels: nominal stays line-to-line ---------------------------------
    def test_explicit_daily_target_wins_over_capacity_derivation(self):
        self.mfm.rated_capacity_kva = 3200
        self.mfm.save()
        cfg = MfmConfig.objects.create(mfm=self.mfm, energy_target_kwh_today=25000)
        self.assertEqual(resolve_mfm_config(MFM.objects.get(pk=self.mfm.pk))[
            'energy_target_kwh_today'], 25000)
        cfg.energy_target_kwh_today = 0
        cfg.save()
        self.assertEqual(resolve_mfm_config(MFM.objects.get(pk=self.mfm.pk))[
            'energy_target_kwh_today'], 0)

    def test_panel_nominal_is_line_to_line_by_default(self):
        cfg = resolve_panel_config(self.panel)
        self.assertEqual(cfg['nominal_voltage_v'], 415.0)
        self.assertEqual(cfg['voltage_nominal_v'], 415.0)

    def test_panel_nominal_override_is_verbatim(self):
        PanelConfig.objects.create(group=self.panel, nominal_voltage_v=11000.0)
        cfg = resolve_panel_config(self.panel)
        self.assertEqual(cfg['nominal_voltage_v'], 11000.0)
        self.assertEqual(cfg['voltage_nominal_v'], 11000.0)

    # --- meters: nominal ships line-to-neutral at 1 dp ----------------------
    def test_meter_nominal_is_line_to_neutral(self):
        cfg = resolve_mfm_config(self.mfm)
        self.assertEqual(cfg['nominal_voltage_v'], 239.6)
        self.assertEqual(cfg['voltage_nominal_v'], 239.6)
        # 239.6 recovers to exactly 415 when the FE re-multiplies by sqrt(3) (BE-62)
        self.assertEqual(round(239.6 * math.sqrt(3)), 415)

    # --- ratings derived from the meter's own kVA ---------------------------
    def test_rated_current_derived_from_rated_kva(self):
        MfmConfig.objects.create(mfm=self.mfm, device_type='lt_panel', rated_kva=1000.0)
        cfg = resolve_mfm_config(MFM.objects.get(pk=self.mfm.pk))
        expected_a = round(1000.0 * 1000.0 / (math.sqrt(3) * 415.0))
        self.assertEqual(cfg['rated_kva'], 1000.0)
        self.assertEqual(cfg['rated_current_a'], expected_a)
        self.assertEqual(cfg['rated_kw'], round(1000.0 * 0.9))
        self.assertEqual(cfg['current_high_threshold_a'], round(expected_a * 1.2))

    def test_explicit_rating_overrides_the_derivation(self):
        MfmConfig.objects.create(mfm=self.mfm, device_type='lt_panel',
                                 rated_kva=1000.0, rated_current_a=1234.0)
        cfg = resolve_mfm_config(MFM.objects.get(pk=self.mfm.pk))
        self.assertEqual(cfg['rated_current_a'], 1234.0)

    # --- frontend key names --------------------------------------------------
    def test_frontend_config_keys_present(self):
        for cfg in (resolve_panel_config(self.panel), resolve_mfm_config(self.mfm)):
            self.assertIn('voltage_nominal_v', cfg)
            self.assertIn('ieee_519_voltage_thd_limit_pct', cfg)
            self.assertIn('ieee_519_current_thd_limit_pct', cfg)
            self.assertIn('ieee_519_individual_harmonic_limit_pct', cfg)
            self.assertEqual(cfg['ieee_519_individual_harmonic_limit_pct'], 8.0)
            self.assertEqual(cfg['flicker_pst_limit'], 1.0)
            self.assertEqual(cfg['crest_factor_ideal'], 1.414)

    def test_equipment_then_mfm_then_direct_override_precedence(self):
        kind = PanelType.objects.get(code='lt_panel')
        equipment = Equipment.objects.create(
            key='load-a', name='Load A', panel_type=kind, metered=True)
        EquipmentConfig.objects.create(
            equipment=equipment, rated_kva=900, nominal_voltage_v=400)
        MfmConfig.objects.create(
            mfm=self.mfm, rated_kva=1000, nominal_voltage_v=415)
        MFM.objects.filter(pk=self.mfm.pk).update(
            equipment=equipment, rated_capacity_kva=1100, nominal_voltage_v=440)
        cfg = resolve_mfm_config(MFM.objects.get(pk=self.mfm.pk))
        self.assertEqual(cfg['rated_kva'], 1100)
        self.assertEqual(cfg['voltage_nominal_v'], round(440 / math.sqrt(3), 1))

    def test_threshold_and_breaker_metadata_are_exposed(self):
        MfmThreshold.objects.create(
            mfm=self.mfm, i_thd_limit_pct=9, v_thd_limit_pct=7,
            individual_harmonic_limit_pct=4, overview_pf_good=.97)
        Breaker.objects.create(mfm=self.mfm, breaker_type='VCB', rating_a=1250)
        cfg = resolve_mfm_config(MFM.objects.get(pk=self.mfm.pk))
        self.assertEqual(cfg['ieee_519_current_thd_limit_pct'], 9)
        self.assertEqual(cfg['ieee_519_voltage_thd_limit_pct'], 7)
        self.assertEqual(cfg['ieee_519_individual_harmonic_limit_pct'], 4)
        self.assertEqual(cfg['overview_pf_good'], .97)
        self.assertEqual((cfg['breaker_type'], cfg['breaker_rating_a']), ('VCB', 1250))


class ResolveForIdTests(TestCase):
    def setUp(self):
        self.panel = Group.objects.create(
            logger_id='grp_mcc2', name='MCC-2', slug='mcc-2', is_panel=True)
        self.section = Group.objects.create(
            logger_id='grp_yard', name='Yard', slug='yard', is_panel=False)
        self.mfm = MFM.objects.create(
            logger_id='dev_02', name='MFM-02', slug='mfm-02')

    def test_panel_id_resolves_to_the_group(self):
        subject, mfm_type, cfg = resolve_for_id(self.panel.id)
        self.assertEqual(subject, self.panel)
        self.assertEqual(mfm_type, 'distribution_panel')
        self.assertEqual(cfg['voltage_nominal_v'], 415.0)

    def test_meter_id_resolves_to_the_mfm(self):
        subject, mfm_type, cfg = resolve_for_id(self.mfm.eid)
        self.assertEqual(subject, self.mfm)
        self.assertEqual(mfm_type, 'lt_panel')
        self.assertEqual(cfg['voltage_nominal_v'], 239.6)

    def test_transformer_device_type_selects_the_transformer_frame(self):
        MfmConfig.objects.create(mfm=self.mfm, device_type='transformer')
        _, mfm_type, _ = resolve_for_id(self.mfm.eid)
        self.assertEqual(mfm_type, 'transformer')

    def test_plain_group_and_unknown_ids_resolve_to_nothing(self):
        self.assertEqual(resolve_for_id(self.section.id), (None, None, None))
        self.assertEqual(resolve_for_id(999_999), (None, None, None))
        self.assertEqual(resolve_for_id(FEEDER_ID_OFFSET + 999_999), (None, None, None))

    def test_retired_rows_resolve_to_nothing(self):
        MFM.objects.filter(pk=self.mfm.pk).update(active=False)
        self.assertEqual(resolve_for_id(FEEDER_ID_OFFSET + self.mfm.pk), (None, None, None))

    def test_mfm_eid_is_offset_plus_id(self):
        self.assertEqual(self.mfm.eid, FEEDER_ID_OFFSET + self.mfm.id)


class MembershipRuleTests(TestCase):
    """clean(): role required iff the group is a panel."""

    def setUp(self):
        self.panel = Group.objects.create(
            logger_id='grp_p', name='Panel', slug='panel', is_panel=True)
        self.section = Group.objects.create(
            logger_id='grp_s', name='Section', slug='section', is_panel=False)
        self.mfm = MFM.objects.create(logger_id='dev_m', name='M', slug='m')

    def test_panel_membership_requires_a_role(self):
        with self.assertRaises(ValidationError):
            Membership(group=self.panel, mfm=self.mfm).clean()
        Membership(group=self.panel, mfm=self.mfm, role='incoming').clean()

    def test_plain_group_membership_rejects_a_role(self):
        with self.assertRaises(ValidationError):
            Membership(group=self.section, mfm=self.mfm, role='incoming').clean()
        Membership(group=self.section, mfm=self.mfm).clean()
