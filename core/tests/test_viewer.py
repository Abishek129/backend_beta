from django.test import TestCase

from core.models import (Equipment, EquipmentConfig, MFM, PanelType,
                         ViewerAsset, ViewerRule, ViewerSetting)


class ViewerEndpointTests(TestCase):
    def setUp(self):
        kind = PanelType.objects.get(code='transformer')
        self.equipment = Equipment.objects.create(
            key='tx-01', name='Transformer 1', panel_type=kind, metered=True)
        EquipmentConfig.objects.create(equipment=self.equipment, rating='2500kva')
        self.mfm = MFM.objects.create(
            name='Tf-1 LV', slug='tf-1-lv', equipment=self.equipment)
        self.asset = ViewerAsset.objects.create(
            slug='transformer-2500', label='Transformer 2500 kVA',
            url='/media/objects/transformer-2500.glb',
            defaults={'camera': {'zoom': 2}, 'lighting': {'intensity': 1}})
        ViewerSetting.objects.create(
            key='viewer_defaults', value={'camera': {'position': [1, 2, 3]}, 'toneMap': 'aces'})

    def test_key_rule_resolves_object_and_deep_merged_viewer(self):
        ViewerRule.objects.create(
            for_key='tx-01', asset=self.asset,
            preset={'camera': {'zoom': 4}, 'bloom': {'enabled': True}})
        body = self.client.get('/api/equipment/key/tx-01/viewer/?default=panel').json()
        self.assertEqual(body['equipment']['key'], 'tx-01')
        self.assertEqual(body['object']['slug'], 'transformer-2500')
        self.assertEqual(body['viewer']['camera'], {'position': [1, 2, 3], 'zoom': 4})
        self.assertEqual(body['viewer']['toneMap'], 'aces')

    def test_type_and_rating_rule_is_the_second_resolution_level(self):
        ViewerRule.objects.create(
            for_type='transformer', rating='2500kva', asset=self.asset)
        body = self.client.get('/api/equipment/key/tx-01/viewer/').json()
        self.assertEqual(body['object']['url'], '/media/objects/transformer-2500.glb')

    def test_config_uses_mapped_equipment_identity(self):
        body = self.client.get(f'/api/mfm/{self.mfm.eid}/config/').json()
        self.assertEqual(body['panel_id'], 'tx-01')
        self.assertEqual(body['config_table'], 'equipment_config')

    def test_unknown_equipment_is_404(self):
        self.assertEqual(self.client.get('/api/equipment/key/missing/viewer/').status_code, 404)
