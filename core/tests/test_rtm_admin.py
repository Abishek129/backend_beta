from django.contrib.auth import get_user_model
from django.test import TestCase
from core.models import Group, RtmThreshold


class RtmMetricAdminTests(TestCase):
    url = '/admin/core/rtmthreshold/'

    def setUp(self):
        self.client.force_login(get_user_model().objects.create_superuser('rtm-admin', password='test'))
        for name in ('Panel A', 'Panel B'):
            group = Group.objects.create(name=name, slug=name.lower().replace(' ', '-'), is_panel=True)
            for metric, bounds in [('pf', (.98, .95, .9, .85)), ('kva', (40, 60, 80, 95))]:
                RtmThreshold.objects.create(group=group, metric=metric,
                    low_max=bounds[0], normal_max=bounds[1], moderate_max=bounds[2], high_max=bounds[3])

    def test_default_and_metric_buttons(self):
        response = self.client.get(self.url, follow=True)
        self.assertEqual(response.redirect_chain[0][0], self.url + '?metric__exact=pf')
        self.assertContains(response, 'Power Factor thresholds')
        self.assertContains(response, '?metric__exact=kva')
        self.assertContains(response, 'aria-current="page"')
        self.assertEqual(response.context['cl'].result_count, 2)
        self.assertEqual({r.metric for r in response.context['cl'].result_list}, {'pf'})

    def test_apparent_power_lists_all_panels_and_can_save(self):
        url = self.url + '?metric__exact=kva'
        response = self.client.get(url)
        self.assertContains(response, 'Apparent Power thresholds')
        formset = response.context['cl'].formset
        data = {'_save': 'Save', 'form-TOTAL_FORMS': len(formset.forms),
                'form-INITIAL_FORMS': len(formset.forms), 'form-MAX_NUM_FORMS': 1000}
        for i, form in enumerate(formset.forms):
            row = form.instance
            data[f'form-{i}-id'] = row.pk
            for field in ('low_max', 'normal_max', 'moderate_max', 'high_max'):
                data[f'form-{i}-{field}'] = 45 if field == 'low_max' else getattr(row, field)
        response = self.client.post(url, data)
        self.assertEqual(response.status_code, 302)
        self.assertEqual(list(RtmThreshold.objects.filter(metric='kva').values_list('low_max', flat=True)), [45, 45])
        self.assertEqual(list(RtmThreshold.objects.filter(metric='pf').values_list('low_max', flat=True)), [.98, .98])
