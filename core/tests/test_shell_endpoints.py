from django.test import TestCase

from core.models import ViewerAsset


class ShellEndpointTests(TestCase):
    def test_empty_non_ems_trees_have_stable_shape(self):
        for path in ('/api/bms/', '/api/assets/'):
            response = self.client.get(path)
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.json(), {'count': 0, 'tree': []})

    def test_catalog_contains_only_active_assets(self):
        ViewerAsset.objects.create(slug='panel', label='Panel', url='/media/panel.glb')
        ViewerAsset.objects.create(slug='old', label='Old', url='/media/old.glb', active=False)
        body = self.client.get('/api/kit-preview/catalog/').json()
        self.assertEqual(body['count'], 1)
        self.assertEqual(body['assets'][0]['slug'], 'panel')
