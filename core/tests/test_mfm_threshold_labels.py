from django.contrib.auth import get_user_model
from django.test import TestCase
from core.models import MFM, MfmThreshold


class MfmThresholdLabelTests(TestCase):
    def setUp(self):
        self.client.force_login(get_user_model().objects.create_superuser('labels-admin', password='test'))
        self.meter = MFM.objects.create(name='Test meter', slug='test-meter')
        self.row = MfmThreshold.objects.create(mfm=self.meter, overview_phase_watch_pct=7)

    def test_kpi_labels_in_grid_form_and_inline(self):
        for url in ('/admin/core/mfmthreshold/',
                    f'/admin/core/mfmthreshold/{self.row.pk}/change/',
                    f'/admin/core/mfm/{self.meter.pk}/change/'):
            response = self.client.get(url)
            self.assertEqual(response.status_code, 200)
            for label in ('Current Unbalance — Watch above (%)',
                          'Power Factor — Good at or above',
                          'Grid Frequency — Nominal (Hz)',
                          'Rated Capacity Utilization — Critical above (%)',
                          'Energy Consumption Today — Elevated above target usage (%)'):
                self.assertContains(response, label)
        self.row.refresh_from_db()
        self.assertEqual(self.row.overview_phase_watch_pct, 7)

    def test_field_identifiers_and_nullability_unchanged(self):
        field = MfmThreshold._meta.get_field('overview_phase_watch_pct')
        self.assertEqual(field.column, 'overview_phase_watch_pct')
        self.assertTrue(field.null)
        self.assertTrue(field.blank)

    def test_frozen_grid_keeps_editable_form(self):
        response = self.client.get('/admin/core/mfmthreshold/')
        self.assertTemplateUsed(response, 'admin/core/mfmthreshold/change_list.html')
        self.assertContains(response, 'class="mfm-threshold-grid"')
        self.assertContains(response, 'position: sticky;')
        self.assertContains(response, 'max-height: 65vh;')
        self.assertContains(response, 'name="form-0-overview_phase_watch_pct"')
        self.assertContains(response, 'name="_save"')
        self.assertContains(response, 'name="csrfmiddlewaretoken"')

    def test_page_filter_selects_columns_not_meters(self):
        for page, included, excluded in (
            ('overview', 'overview_pf_good', 'i_thd_limit_pct'),
            ('power-quality', 'i_thd_limit_pct', 'overview_pf_good'),
            ('voltage-current', 'overview_phase_watch_pct', 'overview_pf_good'),
        ):
            response = self.client.get('/admin/core/mfmthreshold/', {'threshold_page': page})
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.context['cl'].result_count, 1)
            self.assertContains(response, f'name="form-0-{included}"')
            self.assertNotContains(response, f'name="form-0-{excluded}"')

    def test_filtered_save_preserves_hidden_thresholds(self):
        url = '/admin/core/mfmthreshold/?threshold_page=power-quality'
        response = self.client.get(url)
        formset = response.context['cl'].formset
        data = {'_save': 'Save', 'form-TOTAL_FORMS': 1, 'form-INITIAL_FORMS': 1,
                'form-MAX_NUM_FORMS': 1000, 'form-0-id': self.row.pk}
        for field in formset.forms[0].fields:
            if field != 'id':
                data[f'form-0-{field}'] = ''
        data['form-0-i_thd_limit_pct'] = 12
        self.assertEqual(self.client.post(url, data).status_code, 302)
        self.row.refresh_from_db()
        self.assertEqual(self.row.i_thd_limit_pct, 12)
        self.assertEqual(self.row.overview_phase_watch_pct, 7)
