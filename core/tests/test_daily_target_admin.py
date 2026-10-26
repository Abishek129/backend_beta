from django.contrib.auth import get_user_model
from django.test import TestCase
from core.admin import MfmNameplateForm
from core.config import resolve_mfm_config
from core.models import MFM, MfmConfig


class DailyTargetAdminTests(TestCase):
    def test_nameplate_form_saves_explicit_target(self):
        meter = MFM.objects.create(name='Daily target', slug='daily-target', rated_capacity_kva=3200)
        cfg = MfmConfig.objects.create(mfm=meter)
        form = MfmNameplateForm(instance=cfg, data={
            'mfm': meter.pk, 'device_type': 'lt_panel', 'energy_target_kwh_today': '25000',
        })
        self.assertTrue(form.is_valid(), form.errors)
        form.save()
        self.assertEqual(resolve_mfm_config(MFM.objects.get(pk=meter.pk))['energy_target_kwh_today'], 25000)
        self.client.force_login(get_user_model().objects.create_superuser('target-admin', password='test'))
        response = self.client.get(f'/admin/core/mfm/{meter.pk}/change/')
        self.assertContains(response, 'Daily energy target (kWh)')
        self.assertContains(response, 'MFM nameplate configuration')
        self.assertContains(response, 'name="config-0-energy_target_kwh_today"')
