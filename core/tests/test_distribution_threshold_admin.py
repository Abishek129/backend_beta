from django.contrib.auth import get_user_model
from django.test import TestCase
from core.models import Group, PanelConfig


class DistributionThresholdAdminTests(TestCase):
    url = '/admin/core/distributionpanelthreshold/'

    def setUp(self):
        self.client.force_login(get_user_model().objects.create_superuser('panel-admin', password='test'))
        self.panel = Group.objects.create(name='Panel', slug='panel', is_panel=True)
        self.row = PanelConfig.objects.create(group=self.panel, vc_vdev_warn_pct=3, thd_i_limit_pct=8)
        non_panel = Group.objects.create(name='Section', slug='section')
        PanelConfig.objects.create(group=non_panel)

    def test_frozen_grid_and_page_columns(self):
        for page, expected in (
            ('', {'vc_vdev_warn_pct', 'vc_vdev_danger_pct', 'vc_iunbal_warn_pct',
                  'vc_iunbal_danger_pct', 'thd_i_limit_pct', 'thd_v_limit_pct',
                  'true_pf_min', 'neutral_current_limit_a'}),
            ('voltage-current', {'vc_vdev_warn_pct', 'vc_vdev_danger_pct',
                                 'vc_iunbal_warn_pct', 'vc_iunbal_danger_pct'}),
            ('power-quality', {'thd_i_limit_pct', 'thd_v_limit_pct',
                               'true_pf_min', 'neutral_current_limit_a'}),
        ):
            response = self.client.get(self.url, {'threshold_page': page} if page else {})
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.context['cl'].result_count, 1)
            self.assertEqual(set(response.context['cl'].formset.forms[0].fields) - {'id'}, expected)
            self.assertTemplateUsed(response, 'admin/core/distributionpanelthreshold/change_list.html')
            self.assertContains(response, 'position: sticky;')
            self.assertContains(response, '.field-group')
            self.assertContains(response, 'name="_save"')
            self.assertContains(response, 'Distribution panel names stay on the left')

    def test_saving_one_page_preserves_other_page(self):
        url = self.url + '?threshold_page=power-quality'
        data = {'_save': 'Save', 'form-TOTAL_FORMS': 1, 'form-INITIAL_FORMS': 1,
                'form-MAX_NUM_FORMS': 1000, 'form-0-id': self.row.pk,
                'form-0-thd_i_limit_pct': 12, 'form-0-thd_v_limit_pct': '',
                'form-0-true_pf_min': '', 'form-0-neutral_current_limit_a': ''}
        self.assertEqual(self.client.post(url, data).status_code, 302)
        self.row.refresh_from_db()
        self.assertEqual(self.row.thd_i_limit_pct, 12)
        self.assertEqual(self.row.vc_vdev_warn_pct, 3)
