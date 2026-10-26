from django.core.exceptions import ValidationError
from django.test import TestCase

from core.equipment import (ensure_mfm_equipment, ensure_panel_equipment,
                            ensure_primary_reference)
from core.models import (Breaker, Equipment, Group, Membership, MFM, MfmConfig,
                         MfmThreshold, PanelConfig, PanelType)


class EquipmentMetadataTests(TestCase):
    def test_equipment_requires_exactly_one_type_family(self):
        row = Equipment(key='bad', name='Bad')
        with self.assertRaises(ValidationError):
            row.full_clean()

    def test_panel_and_mfm_metadata_are_created_idempotently(self):
        panel = Group.objects.create(name='PCC-1', slug='pcc-1', is_panel=True)
        PanelConfig.objects.create(group=panel, panel_kind='pcc_panel')
        mfm = MFM.objects.create(name='Feeder', slug='feeder')
        MfmConfig.objects.create(
            mfm=mfm, rated_kva=1000, nominal_voltage_v=415,
            rated_current_a=1250, frequency_hz=50)
        Membership.objects.create(group=panel, mfm=mfm, role='outgoing')

        panel_equipment = ensure_panel_equipment(panel)
        measured = ensure_mfm_equipment(mfm)
        reference = ensure_primary_reference(mfm)
        ensure_panel_equipment(panel)
        ensure_mfm_equipment(MFM.objects.select_related('config', 'equipment').get(pk=mfm.pk))

        mfm.refresh_from_db()
        self.assertEqual(panel_equipment.panel_type.code, 'pcc_panel')
        self.assertEqual(mfm.equipment, measured)
        self.assertEqual(reference, panel)
        self.assertEqual(mfm.reference, panel)
        self.assertEqual(mfm.rated_capacity_kva, 1000)
        self.assertEqual(MfmThreshold.objects.filter(mfm=mfm).count(), 1)
        self.assertEqual(Breaker.objects.get(mfm=mfm).rating_a, 1250)

    def test_primary_reference_does_not_remove_other_memberships(self):
        first = Group.objects.create(name='A', slug='a', is_panel=True)
        second = Group.objects.create(name='B', slug='b', is_panel=True)
        mfm = MFM.objects.create(name='Shared', slug='shared')
        Membership.objects.create(group=first, mfm=mfm, role='outgoing', position=0)
        Membership.objects.create(group=second, mfm=mfm, role='incoming', position=1)
        ensure_primary_reference(mfm)
        self.assertEqual(mfm.reference, first)
        self.assertEqual(mfm.memberships.count(), 2)

    def test_specific_equipment_type_beats_placeholder_lt_config(self):
        transformer = PanelType.objects.get(code='transformer')
        equipment = Equipment.objects.create(
            key='tx-1', name='TX-1', panel_type=transformer, metered=True)
        mfm = MFM.objects.create(name='TX meter', slug='tx-meter', equipment=equipment)
        MfmConfig.objects.create(mfm=mfm)  # auto-created/default placeholder type
        self.assertEqual(mfm.equipment.panel_type.code, 'transformer')
